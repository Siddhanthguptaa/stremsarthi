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

5. Weak faces. A blurred, small or turned-away face scores 0.3-0.6 on the 640x360 copy and drops in and out of
   the score threshold; a gap longer than the planner's 0.8 s hold would let it out unmasked. When a masked,
   not-allowlisted track is missed by the 640x360 pass, ``_redetect`` looks for it again in a padded window cut
   from the full-resolution frame. A face found that way keeps the track alive and is masked
   (``reason="face_redetected"``), but it is never allowed, never embedded and does not refresh the track's
   identity state: the next normal sighting after the gap still owes a fresh SFace check. Allowlisted tracks
   are not searched, so an enrolled face behaves exactly as without this step (``params["redetect"]=False``
   switches the step off).

Padding. The detector grows each box by 20 % (10 % per side; ``params["dilate"]``). ``MaskPlanner`` then pads
faces by 10 % of the box per side. Effective mask: about 1.44x the YuNet box per axis (22 % per side), i.e.
the spec's "20 %" applied twice. Set ``params["dilate"] = 0`` to get 1.2x.

Detections carry codes only (``extra["reason"]``); embeddings, crops and face ids never leave this module,
and the 112x112 alignment crop lives only for the length of one embedding job. The detector never touches
the network.
"""

from __future__ import annotations

import logging
import time
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
from vision.tracking.bytetrack import ByteTracker, ByteTrackParams, Track, iou_matrix
from vision.tracking.hysteresis import TrackVotes

from .core import (
    CROP_SIDE,
    DET_H,
    DET_W,
    EMBEDDING_DIM,
    LOW_LIGHT_LUMA,
    Face,
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
BACKJUMP_S = 0.05  # a frame older than the newest one by more than this means the source clock restarted

# Full-resolution re-search around masked face tracks the 640x360 pass missed (see FacesDetector._redetect).
REDETECT_SCORE_UNSURE = 0.35  # = the planner's face floor with mask_when_unsure (1 - sensitivity - 0.15)
REDETECT_SCORE = 0.5  # = the planner's face floor without mask_when_unsure
REDETECT_PAD = 1.0  # search window: the predicted box plus this many box sizes on every side
REDETECT_SIDES = (CROP_SIDE,)  # the window is resized to this long side (px); further entries are fallbacks
REDETECT_MIN_IOU = 0.15  # a face found in the window must overlap the track's predicted box this much
# tracks searched per run: confirmed ones first, then the longest-unsearched (round robin)
REDETECT_MAX_TRACKS = 2
REDETECT_GIVE_UP = 3  # empty searches after which a track seen only once is left to the planner's hold
# a crop is only started this soon (s) after the run began: keeps the FAST lane p95 inside its budget
REDETECT_START_BY_S = 0.016
MIN_REDETECT_SIDE_PX = 8.0  # a track smaller than this is not worth searching for

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
    last_search: int = -10  # run of the last full-resolution search of this track
    weak_fails: int = 0  # consecutive full-resolution searches that found nothing (see _redetect)


@dataclass(slots=True)
class _Job:
    tid: int
    epoch: int  # tracking epoch it was cut in: a reset (allowlist change, restart) voids it
    force_gen: int
    forced: bool
    aligned: np.ndarray  # the 112x112 crop; dropped as soon as the job is done
    matrix: np.ndarray  # the allowlist as it was when the job was cut
    threshold: float


def _shift_row(row: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """A YuNet row moved by (dx, dy): x, landmark x by ``dx``; y, landmark y by ``dy``."""
    out = np.asarray(row, dtype=np.float32).copy()
    out[[0, 4, 6, 8, 10, 12]] += dx
    out[[1, 5, 7, 9, 11, 13]] += dy
    return out


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
        self._reset_requested = False
        self._last_pts = float("-inf")
        self._allow_version: object = None
        self._allow_matrix: np.ndarray | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: list[Future[tuple[_Job, bool]]] = []
        # settings (see configure)
        self._match_cos = DEFAULT_MATCH_COS
        self._dilate = DEFAULT_DILATE
        self._fps = DEFAULT_FPS
        self._max_embeds = MAX_EMBEDS_PER_RUN
        self._redetect_on = True

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
        self._redetect_on = bool(cfg.params.get("redetect", True))
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
        self._last_pts = float("-inf")
        self._epoch += 1  # voids embedding jobs still in flight
        self._futures.clear()

    def reset_state(self) -> None:
        """Ask for a fresh start (the host calls it when the category is switched off, from another thread):
        tracks, votes and pending embeddings are forgotten at the start of the next run."""
        self._reset_requested = True

    def _reset_identity(self) -> None:
        """Forget tracks, votes and in-flight embedding jobs but keep the loaded allowlist."""
        self._tracker = None
        self._state.clear()
        self._votes.reset()
        self._run = 0
        self._last_pts = float("-inf")
        self._epoch += 1
        self._futures.clear()

    # ------------------------------------------------------------------ allowlist
    def _load_allowlist(self, ctx: DetectContext) -> np.ndarray | None:
        """(n, 128) unit vectors of the enrolled faces, or None for an empty (or unusable) allowlist.

        Cached per ``allowlist.version``; a change drops every vote, so a removed face is masked at once and a
        new one is verified from scratch."""
        assert self._res is not None
        view = ctx.allowlist if ctx.allowlist is not None else self._res.allowlist
        snap = getattr(view, "snapshot", None)
        if snap is not None:
            version, entries = snap()  # one lock: version and entries belong together
        else:
            version, entries = getattr(view, "version", None), None
        if version is None or version != self._allow_version or self._allow_matrix is None:
            vectors = []
            for _face_id, vec in entries if entries is not None else view.embeddings():
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
        if self._reset_requested:
            self._reset_requested = False
            self._reset_tracking()
        face_cfg = self._cfg.categories.get(Category.FACE)
        masking = bool(face_cfg is not None and face_cfg.enabled)
        unsure = bool(face_cfg is not None and face_cfg.mask_when_unsure)
        allowlist = self._load_allowlist(ctx)
        if not masking and allowlist is None:  # nothing to mask and nobody to give a mouth quad
            self._reset_tracking()
            return []
        if frame.pts < self._last_pts - BACKJUMP_S:
            # the source clock restarted (a new still, a looped clip, a reconnect): the tracks and votes belong to
            # another scene, so a face at the same place must not inherit them (a stranger could be allowed by a
            # previous person's votes). The allowlist stays loaded; every face is verified from scratch.
            self._reset_identity()
        self._last_pts = max(self._last_pts, frame.pts)
        self._collect()

        t_run = time.perf_counter()
        self._run += 1
        image: np.ndarray = frame.image
        height, width = image.shape[:2]
        small = cv2.resize(image, (DET_W, DET_H), interpolation=cv2.INTER_AREA)
        low_light = mean_luma(small) < LOW_LIGHT_LUMA
        if low_light:
            small = enhance_lowlight(small)
        rows = self._backend.detect(small, SCORE_THRESHOLD_UNSURE if unsure else SCORE_THRESHOLD)
        faces = faces_from_rows(rows, width / DET_W, height / DET_H)
        late = ctx.deadline is not None and self._res.clock() > ctx.deadline

        if self._tracker is None:
            rate = max(1.0, self._fps / max(1, ctx.stride))
            self._tracker = ByteTracker(
                ByteTrackParams(
                    frame_rate=rate, lost_track_buffer=15, low_threshold=0.0, new_track_threshold=0.0
                )
            )
        # Weakly seen faces: a masked track the 640x360 pass missed this run is searched for again in a
        # full-resolution crop around where it was (see _redetect). Those faces are masked, never allowed.
        n_main = len(faces)
        redetected: list[Face] = []
        if masking and self._redetect_on and not late:
            redetected = self._redetect(image, faces, frame.pts, low_light, unsure, t_run)
            faces = faces + redetected
        boxes = np.array([(f.x1, f.y1, f.x2, f.y2) for f in faces], dtype=np.float64).reshape(-1, 4)
        # a re-detected face was found INSIDE its lost track's search window, so for association it counts as
        # certain (its real score still goes to Detection.confidence and the planner's floor)
        scores = np.array([f.score if i < n_main else 1.0 for i, f in enumerate(faces)], dtype=np.float64)
        observed = self._tracker.update(boxes, scores, frame.pts, payloads=list(range(len(faces))))
        track_of = {int(t.payload): t.track_id for t in observed}
        alive = {t.track_id for t in self._tracker.tracks}
        for tid in [t for t in self._state if t not in alive]:
            del self._state[tid]
        self._votes.prune(alive)

        states = [
            self._touch(track_of[i], (f.x1, f.y1, f.x2, f.y2), frame.pts)
            for i, f in enumerate(faces[:n_main])
        ]
        for i in range(n_main, len(faces)):
            states.append(
                self._touch_weak(track_of[i], (faces[i].x1, faces[i].y1, faces[i].x2, faces[i].y2), frame.pts)
            )

        if allowlist is not None:
            plan = [] if late else self._embed_plan(track_of, states, n_main)
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
            weak = i >= n_main
            allow = allowlist is not None and not weak and not st.pending_force and self._votes.allowed(tid)
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
            elif weak:
                reason = "face_redetected"
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

    # ------------------------------------------------------------------ weak-face re-detection
    def _redetect(
        self, image: np.ndarray, main: list[Face], pts: float, low_light: bool, unsure: bool, t_run: float
    ) -> list[Face]:
        """Search the full-resolution frame for masked face tracks the 640x360 pass missed on this run.

        A face that is blurred, small or turned away scores 0.3-0.6 on the 640x360 copy and flickers around the
        score threshold; when it drops out for longer than the planner's hold (0.8 s) it would go out unmasked.
        For up to ``REDETECT_MAX_TRACKS`` such tracks (confirmed ones first, then the most recently seen) a
        window around the track's predicted box (``REDETECT_PAD`` box sizes each side) is cut from the
        FULL-RESOLUTION frame, resized to ``REDETECT_SIDES`` pixels on its long side and run through YuNet at the
        threshold the planner would accept (``REDETECT_SCORE_UNSURE`` / ``REDETECT_SCORE``). A face found there
        that overlaps the track (IoU at least ``REDETECT_MIN_IOU``) and is not one of the 640x360 faces counts
        as another sighting of that track.

        Only tracks that are not currently allowlisted are searched (an enrolled face stays exactly as before:
        no new mask, no new unmasking), and a face found this way is never allowed and never embedded; the
        caller masks it. Costs nothing on runs where every track was seen (the common case); about 3 ms per
        crop otherwise, at most ``REDETECT_MAX_TRACKS`` crops per run, and none is started later than
        ``REDETECT_START_BY_S`` after the run began (a slow run skips the search; the planner's hold covers it).
        A track first seen only once stops being searched after ``REDETECT_GIVE_UP`` empty searches. The
        window holds pixels only for the length of one YuNet call.
        """
        assert self._tracker is not None and self._backend is not None
        thr = REDETECT_SCORE_UNSURE if unsure else REDETECT_SCORE
        main_boxes = np.array([(f.x1, f.y1, f.x2, f.y2) for f in main], dtype=np.float64).reshape(-1, 4)
        cands: list[tuple[bool, int, float, int, tuple[float, float, float, float]]] = []
        for t in self._tracker.tracks:
            st = self._state.get(t.track_id)
            if st is None or (t.hits < 2 and st.weak_fails >= REDETECT_GIVE_UP):
                continue
            if self._votes.allowed(t.track_id) and not st.pending_force:
                continue
            box = self._predicted_box(t, pts)
            if box[2] - box[0] < MIN_REDETECT_SIDE_PX or box[3] - box[1] < MIN_REDETECT_SIDE_PX:
                continue
            if len(main_boxes) and float(iou_matrix(np.array([box]), main_boxes).max()) >= 0.1:
                continue  # the 640x360 pass saw a face there: the tracker will match it
            cands.append((t.hits >= 2, -st.last_search, t.last_hit_pts, t.track_id, box))
        if not cands:
            return []
        cands.sort(reverse=True)
        found: list[Face] = []
        taken = main_boxes
        for _, _, _, tid, box in cands[:REDETECT_MAX_TRACKS]:
            best: Face | None = None
            for side in REDETECT_SIDES:
                if best is not None or time.perf_counter() - t_run > REDETECT_START_BY_S:
                    break
                best = self._best_in_crop(image, box, side, thr, low_light, taken)
            st = self._state[tid]
            st.last_search = self._run
            if best is None:
                st.weak_fails += 1
            else:
                st.weak_fails = 0
                found.append(best)
                taken = np.vstack([taken, [[best.x1, best.y1, best.x2, best.y2]]])
        return found

    def _best_in_crop(
        self,
        image: np.ndarray,
        box: tuple[float, float, float, float],
        side: int,
        thr: float,
        low_light: bool,
        taken: np.ndarray,
    ) -> Face | None:
        assert self._backend is not None
        height, width = image.shape[:2]
        x1, y1, x2, y2 = box
        px, py = REDETECT_PAD * (x2 - x1), REDETECT_PAD * (y2 - y1)
        cx1, cy1 = int(max(0.0, np.floor(x1 - px))), int(max(0.0, np.floor(y1 - py)))
        cx2, cy2 = int(min(float(width), np.ceil(x2 + px))), int(min(float(height), np.ceil(y2 + py)))
        cw, ch = cx2 - cx1, cy2 - cy1
        if cw < 16 or ch < 16:
            return None
        scale = side / max(cw, ch)
        rw, rh = max(16, round(cw * scale)), max(16, round(ch * scale))
        crop = cv2.resize(
            image[cy1:cy2, cx1:cx2],
            (rw, rh),
            interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
        )
        if low_light:
            crop = enhance_lowlight(crop)
        if (rw, rh) != (side, side):  # a fixed square canvas keeps the detector's input size constant
            crop = cv2.copyMakeBorder(
                crop, 0, side - rh, 0, side - rw, cv2.BORDER_CONSTANT, value=(114, 114, 114)
            )
        rows = self._backend.detect(crop, thr)
        best: Face | None = None
        best_iou = REDETECT_MIN_IOU
        for f in faces_from_rows(rows, cw / rw, ch / rh):
            full = Face(_shift_row(f.row, cx1, cy1))
            fb = (full.x1, full.y1, full.x2, full.y2)
            if len(taken) and float(iou_matrix(np.array([fb]), taken).max()) >= 0.3:
                continue  # that is a face already found on this run
            v = _iou(fb, box)
            if v >= best_iou:
                best, best_iou = full, v
        return best

    def _predicted_box(self, t: Track, pts: float) -> tuple[float, float, float, float]:
        """The track's box moved by its velocity to ``pts`` (at most one box size either way)."""
        x1, y1, x2, y2 = t.box_xyxy
        dt = max(0.0, pts - t.kf_pts)
        dx = float(np.clip(t.velocity_px_s[0] * dt, -(x2 - x1), x2 - x1))
        dy = float(np.clip(t.velocity_px_s[1] * dt, -(y2 - y1), y2 - y1))
        return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    # ------------------------------------------------------------------ per-track bookkeeping
    @staticmethod
    def _owe_check(st: _TrackState) -> None:
        st.pending_force = True
        st.force_gen += 1

    def _touch_weak(self, tid: int, box: tuple[float, float, float, float], pts: float) -> _TrackState:
        """A sighting from the full-resolution re-search. It keeps the track alive and masked but proves
        nothing about identity: the state is left as it was (so the next real sighting after this gap still owes
        a fresh check), and a track that started from such a sighting has never been verified."""
        st = self._state.get(tid)
        if st is None:
            st = self._state[tid] = _TrackState(box, self._run, pts)
            self._owe_check(st)
        return st

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
        st.weak_fails = 0
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
