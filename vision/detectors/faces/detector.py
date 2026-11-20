"""``faces`` detector: YuNet + SFace allowlist on the FAST lane (spec 5.5, T2.6).

Per detection run (every ``detect.stride``-th frame):

1. The 1280x720 frame is shrunk to 640x360 (INTER_AREA); when its mean luma is under 60, CLAHE is applied
   to that copy. YuNet (``cv2.FaceDetectorYN``) finds faces and five landmarks each. Score threshold 0.6, or
   0.5 when the category has ``mask_when_unsure``.
2. A private ``ByteTracker`` gives every face a ``track_id``. Identity is decided PER TRACK: each track has
   its own vote window (``vision.tracking.TrackVotes``), so a stranger standing next to an enrolled person
   never inherits the neighbour's votes (the PrivaStream shared-buffer bug).
3. SFace embeds a face only when it matters: a new track (every run until it is allowed or its window is
   full), a re-check of an allowed track every 5th detection, and a forced re-check when the track could
   have changed owner (it reappeared after a gap, or its box jumped: IoU under 0.6). Cosine similarity
   against ``Resources.allowlist`` gives one vote per embedding (``max cos >= face_match_cos``). The
   alignment crop (0.1 ms) is cut on the lane thread; the 15 ms SFace pass runs on one worker thread
   (``params["embed_async"]``, default on) so the lane stays near the YuNet time. The vote lands on the next
   run. Until it lands the face is simply not allowed, which means masked.
4. ``allow=True`` only when the track's window says so (at least ``face_vote_min`` of the last
   ``face_vote_window`` samples matched, at least 3 samples, not 3 misses in a row) and no forced re-check
   is pending. Everything else (new, unconfirmed, unmatched, empty allowlist) is ``allow=False`` and gets
   masked by the planner. An allowlisted face carries ``aux_quads["mouth"]`` (from the two mouth corners,
   for the engine's MOUTH_OF_SPEAKER) and nothing else leaves this detector.

Padding. The detector grows each box by 20 % (10 % per side; ``params["dilate"]``). ``MaskPlanner`` then pads
faces by 10 % of the box per side. Effective mask: about 1.44x the YuNet box per axis (22 % per side), i.e.
the spec's "20 %" applied twice. Set ``params["dilate"] = 0`` to get 1.2x.

Detections carry codes only (``extra["reason"]``); embeddings, crops and face ids never leave this module,
and the 112x112 alignment crop lives only for the length of one embedding job. The detector never touches
the network.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, ClassVar

import cv2
import numpy as np

from contracts import (
    Category,
    DetectContext,
    Detection,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    Quad,
    Resources,
)
from vision.tracking.bytetrack import ByteTracker, ByteTrackParams
from vision.tracking.hysteresis import TrackVotes

from .core import (
    DET_H,
    DET_W,
    EMBEDDING_DIM,
    LOW_LIGHT_LUMA,
    FaceBackend,
    dilate_box,
    enhance_lowlight,
    faces_from_rows,
    l2_normalise,
    load_models,
    mean_luma,
    mouth_quad,
)

log = logging.getLogger("vision.faces")

DEFAULT_MATCH_COS = 0.40  # UNVERIFIED (spec 5.5; the OpenCV zoo uses 0.363). Tune on the eval data.
DEFAULT_VOTE_WINDOW = 7
DEFAULT_VOTE_MIN = 5
DEFAULT_FPS = 30
DEFAULT_DILATE = 0.20
RECHECK_EVERY = 5  # an allowed track is re-embedded every 5th detection
RETRY_EVERY = 3  # a full-window track that is not allowed is retried this often (it may be a pose change) ...
STRANGER_EVERY = 10  # ... or this often when none of its samples matched (clearly a stranger)
JUMP_IOU = 0.6  # a box that moves less than this IoU between runs may be another person
MAX_GAP_S = 0.35  # not seen for longer than this (a few runs): the owner may have changed
MAX_EMBEDS_PER_RUN = 3  # faces over budget stay masked until the next run
MAX_IN_FLIGHT = 6  # embedding jobs queued on the worker; more are not submitted (those faces stay masked)
SCORE_THRESHOLD = 0.6
SCORE_THRESHOLD_UNSURE = 0.5
MIN_AREA_PX = 4.0

# priorities of an embedding request: lower is served first
_P_FORCED, _P_BUILDING, _P_RETRY, _P_RECHECK = 0, 1, 2, 3


@dataclass(slots=True)
class _TrackState:
    last_box: tuple[float, float, float, float]
    last_run: int
    last_pts: float
    since_embed: int = 0
    pending_force: bool = False  # a re-check is owed: do not show this face until it has passed
    force_gen: int = 0  # bumped every time a re-check becomes owed
    inflight: bool = False


@dataclass(slots=True)
class _Job:
    tid: int
    epoch: int  # tracking epoch it was cut in: a reset (allowlist change, restart) voids it
    force_gen: int
    forced: bool
    aligned: np.ndarray  # the 112x112 crop; dropped as soon as the job is done
    matrix: np.ndarray  # the allowlist as it was when the job was cut
    threshold: float


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class FacesDetector:
    id: ClassVar[str] = "faces"
    lane: ClassVar[Lane] = Lane.FAST
    categories: ClassVar[frozenset[Category]] = frozenset({Category.FACE})
    device: ClassVar[Device] = Device.CPU
    vram_mb: ClassVar[int] = 0  # YuNet and SFace run through cv2.dnn on the CPU

    def __init__(self, backend: FaceBackend | None = None) -> None:
        self._injected = backend
        self._backend: FaceBackend | None = None
        self._res: Resources | None = None
        self._cfg: DetectorConfig | None = None
        self._tracker: ByteTracker | None = None
        self._votes = TrackVotes()
        self._state: dict[int, _TrackState] = {}
        self._run = 0
        self._epoch = 0
        self._allow_version: object = None
        self._allow_matrix: np.ndarray | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: list[Future[tuple[_Job, bool]]] = []
        # settings (see configure)
        self._match_cos = DEFAULT_MATCH_COS
        self._dilate = DEFAULT_DILATE
        self._fps = DEFAULT_FPS
        self._max_embeds = MAX_EMBEDS_PER_RUN

    # ------------------------------------------------------------------ plugin interface
    def _setting(self, name: str, default: Any) -> Any:
        """``DetectorConfig.params`` first, then ``Resources.config.vision``/``.fps`` when the host has one."""
        assert self._cfg is not None
        if name in self._cfg.params:
            return self._cfg.params[name]
        conf = getattr(self._res, "config", None)
        if conf is not None:
            if name == "fps":
                return getattr(conf, "fps", default)
            return getattr(getattr(conf, "vision", None), name, default)
        return default

    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._shutdown_worker()
        self._cfg, self._res = cfg, res
        self._match_cos = float(self._setting("face_match_cos", DEFAULT_MATCH_COS))
        window = int(self._setting("face_vote_window", DEFAULT_VOTE_WINDOW))
        minimum = int(self._setting("face_vote_min", DEFAULT_VOTE_MIN))
        self._votes = TrackVotes(window, minimum)
        self._dilate = float(cfg.params.get("dilate", DEFAULT_DILATE))
        self._fps = int(self._setting("fps", DEFAULT_FPS))
        self._max_embeds = int(cfg.params.get("max_embeds_per_run", MAX_EMBEDS_PER_RUN))
        self._backend = self._injected or load_models(getattr(res, "model_path", None))
        if bool(cfg.params.get("embed_async", True)):
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="faces-embed")
        self._reset_tracking()

    def warmup(self) -> None:
        if self._backend is not None:
            self._backend.warmup()

    def close(self) -> None:
        self._shutdown_worker()
        self._backend = None
        self._res = None
        self._reset_tracking()

    def _shutdown_worker(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
        self._executor = None
        self._futures.clear()

    def _reset_tracking(self) -> None:
        self._tracker = None
        self._state.clear()
        self._votes.reset()
        self._allow_version = None
        self._allow_matrix = None
        self._run = 0
        self._epoch += 1  # voids embedding jobs still in flight
        self._futures.clear()

    # ------------------------------------------------------------------ allowlist
    def _load_allowlist(self, ctx: DetectContext) -> np.ndarray | None:
        """(n, 128) unit vectors of the enrolled faces, or None for an empty (or unusable) allowlist.

        Cached per ``allowlist.version``; a change drops every vote, so a removed face is masked at once and a
        new one is verified from scratch."""
        assert self._res is not None
        view = ctx.allowlist if ctx.allowlist is not None else self._res.allowlist
        version = getattr(view, "version", None)
        if version is None or version != self._allow_version or self._allow_matrix is None:
            vectors = []
            for _face_id, vec in view.embeddings():
                arr = np.asarray(vec, dtype=np.float32).reshape(-1)
                if arr.size == EMBEDDING_DIM and np.all(np.isfinite(arr)):
                    vectors.append(l2_normalise(arr))
            matrix = np.stack(vectors) if vectors else np.zeros((0, EMBEDDING_DIM), np.float32)
            if self._allow_version is not None and version != self._allow_version:
                self._votes.reset()
                self._epoch += 1
                self._futures.clear()
                for st in self._state.values():
                    st.inflight = False
                    self._owe_check(st)
            self._allow_version, self._allow_matrix = version, matrix
        return self._allow_matrix if len(self._allow_matrix) else None

    # ------------------------------------------------------------------ detection
    def detect(self, frame: Frame, ctx: DetectContext) -> list[Detection]:
        if self._backend is None or self._res is None or self._cfg is None:
            raise RuntimeError("faces detector used before configure()")
        face_cfg = self._cfg.categories.get(Category.FACE)
        masking = bool(face_cfg is not None and face_cfg.enabled)
        unsure = bool(face_cfg is not None and face_cfg.mask_when_unsure)
        allowlist = self._load_allowlist(ctx)
        if not masking and allowlist is None:  # nothing to mask and nobody to give a mouth quad
            self._reset_tracking()
            return []
        self._collect()

        self._run += 1
        image: np.ndarray = frame.image
        height, width = image.shape[:2]
        small = cv2.resize(image, (DET_W, DET_H), interpolation=cv2.INTER_AREA)
        low_light = mean_luma(small) < LOW_LIGHT_LUMA
        if low_light:
            small = enhance_lowlight(small)
        rows = self._backend.detect(small, SCORE_THRESHOLD_UNSURE if unsure else SCORE_THRESHOLD)
        faces = faces_from_rows(rows, width / DET_W, height / DET_H)

        if self._tracker is None:
            rate = max(1.0, self._fps / max(1, ctx.stride))
            self._tracker = ByteTracker(
                ByteTrackParams(frame_rate=rate, lost_track_buffer=15, low_threshold=0.0, new_track_threshold=0.0)
            )
        boxes = np.array([(f.x1, f.y1, f.x2, f.y2) for f in faces], dtype=np.float64).reshape(-1, 4)
        scores = np.array([f.score for f in faces], dtype=np.float64)
        observed = self._tracker.update(boxes, scores, frame.pts, payloads=list(range(len(faces))))
        track_of = {int(t.payload): t.track_id for t in observed}
        alive = {t.track_id for t in self._tracker.tracks}
        for tid in [t for t in self._state if t not in alive]:
            del self._state[tid]
        self._votes.prune(alive)

        states = [self._touch(track_of[i], (f.x1, f.y1, f.x2, f.y2), frame.pts) for i, f in enumerate(faces)]

        if allowlist is not None:
            late = ctx.deadline is not None and self._res.clock() > ctx.deadline
            plan = [] if late else self._embed_plan(track_of, states, len(faces))
            if plan:
                source = enhance_lowlight(image) if low_light else image
                for i in plan[: self._max_embeds]:
                    if len(self._futures) >= MAX_IN_FLIGHT:
                        break
                    self._submit(track_of[i], states[i], self._backend.align(source, faces[i].row), allowlist)

        out: list[Detection] = []
        now = self._res.clock()
        for i, f in enumerate(faces):
            tid = track_of[i]
            st = states[i]
            allow = allowlist is not None and not st.pending_force and self._votes.allowed(tid)
            if not allow and not masking:
                continue
            quad = dilate_box(f.x1, f.y1, f.x2, f.y2, self._dilate, width, height)
            if quad.area_bbox() < MIN_AREA_PX:
                continue
            aux: dict[str, Quad] = {}
            if allow:
                mq = mouth_quad(f.landmarks, width, height)
                if mq is not None:
                    aux["mouth"] = mq
            if allow:
                reason = "face_allowlisted"
            elif allowlist is None:
                reason = "face_no_allowlist"
            elif self._votes.samples(tid) == 0 or st.pending_force:
                reason = "face_unverified"
            else:
                reason = "face_unmatched"
            out.append(
                Detection(
                    detector=self.id,
                    category=Category.FACE,
                    quad=quad,
                    confidence=float(min(1.0, max(0.0, f.score))),
                    frame_seq=frame.seq,
                    pts=frame.pts,
                    completed_at=now,
                    track_id=tid,
                    allow=allow,
                    aux_quads=aux,
                    extra={"reason": reason},
                )
            )
        return out

    # ------------------------------------------------------------------ per-track bookkeeping
    @staticmethod
    def _owe_check(st: _TrackState) -> None:
        st.pending_force = True
        st.force_gen += 1

    def _touch(self, tid: int, box: tuple[float, float, float, float], pts: float) -> _TrackState:
        """Update the track's state for this run and decide whether its identity must be re-verified."""
        st = self._state.get(tid)
        if st is None:
            st = self._state[tid] = _TrackState(box, self._run, pts)
            return st
        gap = (self._run - st.last_run > 1) or (pts - st.last_pts > MAX_GAP_S)
        if gap or _iou(st.last_box, box) < JUMP_IOU:
            self._owe_check(st)
        st.last_box, st.last_run, st.last_pts = box, self._run, pts
        st.since_embed += 1
        return st

    def _embed_plan(self, track_of: dict[int, int], states: list[_TrackState], n: int) -> list[int]:
        """Face indices to embed this run, most urgent first."""
        wanted: list[tuple[int, int, int]] = []
        for i in range(n):
            tid, st = track_of[i], states[i]
            if st.inflight:
                continue
            samples = self._votes.samples(tid)
            allowed = self._votes.allowed(tid)
            if st.pending_force:
                prio = _P_FORCED
            elif samples < self._votes.window and not allowed:
                prio = _P_BUILDING
            elif allowed:
                if st.since_embed < RECHECK_EVERY:
                    continue
                prio = _P_RECHECK
            else:
                every = STRANGER_EVERY if self._votes.matches(tid) == 0 else RETRY_EVERY
                if st.since_embed < every:
                    continue
                prio = _P_RETRY
            wanted.append((prio, samples, i))
        wanted.sort()
        return [i for _, _, i in wanted]

    # ------------------------------------------------------------------ embedding jobs
    def _submit(self, tid: int, st: _TrackState, aligned: np.ndarray, matrix: np.ndarray) -> None:
        job = _Job(tid, self._epoch, st.force_gen, st.pending_force, aligned, matrix, self._match_cos)
        st.since_embed = 0
        if self._executor is None:
            self._apply(job, self._match(job))
            return
        st.inflight = True
        self._futures.append(self._executor.submit(self._run_job, job))

    def _match(self, job: _Job) -> bool:
        assert self._backend is not None
        emb = self._backend.embed_aligned(job.aligned)
        return bool(float(np.max(job.matrix @ emb)) >= job.threshold)

    def _run_job(self, job: _Job) -> tuple[_Job, bool]:
        try:
            return job, self._match(job)
        except Exception as exc:  # noqa: BLE001 - a failed check is a non-match: the face stays masked
            log.warning("face embedding failed (%s); treating as no match", type(exc).__name__)
            return job, False

    def _collect(self) -> None:
        """Apply the votes of embedding jobs that finished since the last run (async mode)."""
        pending: list[Future[tuple[_Job, bool]]] = []
        for fut in self._futures:
            if not fut.done():
                pending.append(fut)
                continue
            if fut.cancelled():
                continue
            job, matched = fut.result()
            self._apply(job, matched)
        self._futures = pending

    def _apply(self, job: _Job, matched: bool) -> None:
        st = self._state.get(job.tid)
        if st is None or job.epoch != self._epoch:
            return  # the track is gone, or the allowlist changed since the crop was cut
        st.inflight = False
        was_allowed = self._votes.allowed(job.tid)
        if job.forced and not matched:
            # the face on this track failed a check it owed: whatever the track earned does not apply
            self._votes.forget(job.tid)
        self._votes.add(job.tid, matched)
        if job.force_gen == st.force_gen:
            st.pending_force = False
        if was_allowed and not matched and not job.forced:
            # an allowed track just missed its periodic check: hide it and look again on the next run, so a
            # face that took over the track (a camera cut, a swap) is masked within a run instead of after
            # three more periodic checks
            self._owe_check(st)
