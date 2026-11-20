"""The deadline engine: a transport-free frame clock that fails toward privacy.

Adapted from IRL Shield ``shield/engine.py`` (MIT, Copyright (c) 2026 Elan Nash; licence text in
``engine/THIRD_PARTY_LICENSES/irl-shield/LICENSE``). Kept: the deadline rule, the bounded frame
ring, ``poll`` capped at 4 frames, late-ingest discard, ingest-gap protection, and the "a result
only counts if its motion path finished before the deadline" gate. Changed: N lanes with a
per-lane maximum age instead of a fixed fast/accurate pair; ``contracts.LaneResult`` instead of
``Detection``; ``HoldSource``-keyed holds with retroactive bookkeeping; the HOLD fence;
``FrameDecision`` instead of a rendered frame (painting belongs to the compositor).

Rules (docs/specs/engine.md section 5):
* deadline of frame ``i`` = ``origin + pts_i + D`` with ``origin = first.arrival - first.pts``;
* a frame is LIVE only if every active lane has a connected, on-time, ``ok`` result inside its
  window and maximum age; otherwise PROTECT with a reason code. The engine starts in PROTECT and
  refuses LIVE with zero active lanes;
* masks come from every connected result within the lane's maximum age plus any request whose
  ``[pts_start, pts_end]`` covers the frame, projected through the motion path (requests with
  the same ``request_id`` are de-duplicated, nearest anchor wins);
* HOLD > PROTECT > LIVE. After a hold, every frame captured up to the capture high-water mark
  stays slated (the fence), so stale buffered frames cannot leak when the hold is released.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import numpy as np

from contracts import (
    Config,
    Frame,
    HoldCommand,
    HoldSource,
    Lane,
    LaneResult,
    MaskKind,
    MaskRequest,
    Quad,
)

from .control import HOLD, LIVE, PROTECT, Control
from .mouth import resolve_mouth_quads

log = logging.getLogger("streamsarthi.engine.deadline")

OutputState = Literal["LIVE", "PROTECT", "HOLD"]
# "frame": the frame itself (LIVE, masks painted). "mean": PROTECT, mean-colour fill (first
# ``protect_fill_s``). "slate": the privacy slate.
Presentation = Literal["frame", "mean", "slate"]

DEFAULT_MAX_AGE: Mapping[Lane, int] = {Lane.FAST: 6, Lane.ACCURATE: 12, Lane.SLOW: 60}
PTS_RESET_SECONDS = 1.0  # a PTS step back by more than this restarts the session
_RANGE_EPS = 1e-9
_STAGES = ("motion_submit", "coverage", "poll")


class MotionPath(Protocol):
    """What the engine needs from ``engine.motion`` (``MotionWorker`` or ``IdentityMotion``)."""

    def add(self, index: int, pixels: Any) -> None: ...

    def project_many(
        self, quads: list[Any], source: int, target: int, deadline: float | None = None
    ) -> list[Any] | None: ...


@dataclass(frozen=True, slots=True)
class ProjectedMask:
    """A mask request with its quads already projected onto the frame being emitted."""

    request: MaskRequest
    quads: tuple[Quad, ...]


@dataclass(frozen=True, slots=True)
class RetroRange:
    """PTS range of audio to zero because of a retroactive hold (``pts_end`` is the press time)."""

    source: HoldSource
    pts_start: float
    pts_end: float


@dataclass(slots=True)
class FrameDecision:
    """What to emit for one frame. ``image`` is the shared read-only ring pixels (never mutate)."""

    seq: int
    pts: float
    image: Any | None  # None only when nothing was ever received (cannot be shown anyway)
    state: OutputState
    reason: str  # machine code: covered, missing-fast, ingest-gap, hold-fence, ...
    presentation: Presentation
    masks: tuple[ProjectedMask, ...]
    full_frame: bool
    mouth_requests: tuple[MaskRequest, ...]  # MOUTH_OF_SPEAKER windows active at pts (T3.4)
    deadline: float
    emitted_at: float
    protect_age: float  # seconds since the first non-LIVE frame of this run (0 when LIVE)
    lane_ages: Mapping[str, int | None]  # lane value -> age in frames of its covering result
    gap: bool = False  # no input frame arrived by its deadline
    retro: bool = False  # marked by a retroactive hold
    fenced: bool = False  # slated by the HOLD fence
    arrival: float | None = None
    mouth_quads: tuple[Quad, ...] = ()  # padded mouths of allowlisted faces, projected (engine/mouth.py)

    @property
    def live(self) -> bool:
        return self.state == LIVE

    @property
    def audio_silent(self) -> bool:
        """HOLD silences audio fully; PROTECT passes audio (mutes applied elsewhere)."""
        return self.state == HOLD

    @property
    def emit_lag(self) -> float:
        return self.emitted_at - self.deadline


@dataclass(slots=True)
class _Stored:
    seq: int
    pts: float
    arrival: float
    image: Any


@dataclass(slots=True)
class _LaneStats:
    results: int = 0
    failures: int = 0
    discarded: int = 0


@dataclass(slots=True)
class _Coverage:
    ok: bool
    reason: str
    masks: dict[str, tuple[float, ProjectedMask]] = field(default_factory=dict)
    full_frame: bool = False
    mouth: dict[str, MaskRequest] = field(default_factory=dict)
    ages: dict[str, int | None] = field(default_factory=dict)


def _covers(request: MaskRequest, pts: float) -> bool:
    return request.pts_start - _RANGE_EPS <= pts <= request.pts_end + _RANGE_EPS


class DeadlineEngine:
    """Thread-safe: ``submit``, ``submit_result``, ``hold`` and ``poll`` may run on different threads."""

    def __init__(
        self,
        lanes: Iterable[Lane],
        *,
        fps: float = 30.0,
        delay_seconds: float = 4.0,
        max_age: Mapping[Lane, int] | None = None,
        window_before: int = 6,
        window_after: int = 30,
        max_buffer_frames: int | None = None,
        max_poll_frames: int = 4,
        retro_seconds: float = 0.0,
        protect_fill_s: float = 1.0,
        motion: MotionPath | None = None,
        control: Control | None = None,
        copy_frames: bool = True,
    ) -> None:
        fps, delay_seconds = float(fps), float(delay_seconds)
        if not math.isfinite(fps) or not math.isfinite(delay_seconds) or fps <= 0 or delay_seconds < 0:
            raise ValueError("Invalid source rate or presentation delay")
        if window_before < 0 or window_after < 0:
            raise ValueError("Coverage window must be non-negative")
        self._fps = fps
        self._delay = delay_seconds
        self._before = int(window_before)
        self._after = int(window_after)
        self._max_age: dict[Lane, int] = {**DEFAULT_MAX_AGE, **(dict(max_age) if max_age else {})}
        self._max_buffer = int(max_buffer_frames or math.ceil(fps * (delay_seconds + 1)) + 8)
        self._max_poll = int(max_poll_frames)
        if self._max_buffer < 1 or self._max_poll < 1:
            raise ValueError("Frame buffer and poll batch must have positive capacity")
        self._protect_fill_s = float(protect_fill_s)
        self._copy_frames = copy_frames
        retro = max(0.0, min(float(retro_seconds), delay_seconds - 0.5))
        self.control = control or Control(retro_seconds=retro)
        if control is not None:
            self.control.retro_seconds = retro
        if motion is None:
            from .motion import MotionHistory, MotionWorker

            # The history must reach back from the newest submitted frame to the oldest frame still to be
            # decided, i.e. the whole delay (``fps * D`` frames) plus the look-back window plus jitter. A
            # fixed 120 frames equals ``fps * D`` at D=4 s, so the frame being decided was already evicted
            # and every projection came back unresolved (G1 root cause).
            motion = MotionWorker(
                MotionHistory(
                    max_frames=self.motion_history_frames(fps, delay_seconds, self._before),
                    analysis_width=320,
                    mode="phase",
                    phase_min_response=0.5,
                ),
                capacity=self._max_buffer,
            )
        self.motion: MotionPath = motion
        needed = self.motion_history_frames(fps, delay_seconds, self._before)
        held = getattr(motion, "history_capacity", None)
        if held is not None and held < needed:
            log.warning(
                "motion history keeps %d frames but a %.1f s delay at %.0f fps needs %d: "
                "every projection will be unresolved and every frame PROTECT",
                held,
                delay_seconds,
                fps,
                needed,
            )
        self._lock = threading.RLock()
        self._lanes: tuple[Lane, ...] = ()
        self._timeline: dict[Lane, list[LaneResult]] = {}
        self._lane_stats: dict[Lane, _LaneStats] = {}
        self.set_lanes(lanes)
        self._frames: dict[int, _Stored] = {}
        self._extra: dict[str, MaskRequest] = {}
        self._retro_seqs: set[int] = set()
        self._retro_ranges: list[RetroRange] = []
        self._fence_through = -1
        self._reset_state()
        self._stage: dict[str, deque[float]] = {name: deque(maxlen=4096) for name in _STAGES}
        self._emit_lag: deque[float] = deque(maxlen=4096)
        self.outputs = self.live_frames = self.protect_frames = self.hold_frames = 0
        self.late_ingest = self.buffer_evictions = self.resets = 0
        # Per-frame coverage diagnostics (counters only: no pixels, no PII). ``reason_counts`` is keyed
        # "STATE:reason"; ``lane_diag`` is keyed "<lane>:<what>" (see ``_diagnose``).
        self.reason_counts: Counter[str] = Counter()
        self.lane_diag: Counter[str] = Counter()
        self._miss_distance: dict[str, deque[int]] = {}

    @staticmethod
    def motion_history_frames(fps: float, delay_seconds: float, window_before: int) -> int:
        """Frames of motion history needed so the frame at its deadline can still be projected."""
        return math.ceil(fps * (delay_seconds + 1)) + 8 + int(window_before)

    @classmethod
    def from_config(
        cls,
        config: Config,
        lanes: Iterable[Lane],
        *,
        motion: MotionPath | None = None,
        control: Control | None = None,
        **overrides: Any,
    ) -> DeadlineEngine:
        age = config.engine.lane_max_age
        params: dict[str, Any] = {
            "fps": config.fps,
            "delay_seconds": config.delay_seconds,
            "max_age": {Lane.FAST: age.fast, Lane.ACCURATE: age.accurate, Lane.SLOW: age.slow},
            "window_before": config.engine.window.before,
            "window_after": config.engine.window.after,
            "retro_seconds": config.effective_retro_seconds,
            "protect_fill_s": config.engine.protect_fill_s,
        }
        params.update(overrides)
        return cls(lanes, motion=motion, control=control, **params)

    # ------------------------------------------------------------------ configuration

    def set_lanes(self, lanes: Iterable[Lane]) -> None:
        """Replace the active lane set (a category toggle changed which detectors run)."""
        chosen = tuple(dict.fromkeys(Lane(lane) for lane in lanes))
        with self._lock:
            self._lanes = chosen
            for lane in chosen:
                self._timeline.setdefault(lane, [])
                self._lane_stats.setdefault(lane, _LaneStats())

    @property
    def lanes(self) -> tuple[Lane, ...]:
        return self._lanes

    @property
    def delay_seconds(self) -> float:
        return self._delay

    @property
    def origin(self) -> float | None:
        return self._origin

    def _reset_state(self) -> None:
        self._origin: float | None = None
        self._first_pts = 0.0
        self._first_index = self._next_index = 0
        self._last_index = -1
        self._last_pts: float | None = None
        self._terminal: int | None = None
        self._last_frame: _Stored | None = None
        self._last_pixels: Any | None = None
        self._last_emit: tuple[int, float] | None = None
        self._last_ages: dict[str, int | None] = {}
        self._protect_since: float | None = None

    # ------------------------------------------------------------------ ingest

    def submit(self, frame: Frame, now: float | None = None) -> bool:
        """Add a decoded frame. Returns False when it was discarded (late, or already passed).

        ``now`` is the engine clock at submission (default ``time.monotonic()``); the deadline
        origin comes from ``frame.arrival``.
        """
        now = time.monotonic() if now is None else now
        if not math.isfinite(frame.pts) or not math.isfinite(now) or not math.isfinite(frame.arrival):
            raise ValueError("Frame and clock timestamps must be finite")
        if not isinstance(frame.seq, int) or frame.seq < 0:
            raise ValueError("Frame seq must be a nonnegative integer")
        image = frame.image
        if (
            not isinstance(image, np.ndarray)
            or image.dtype != np.uint8
            or image.ndim != 3
            or image.shape[2] != 3
        ):
            raise ValueError("Frames must be uint8 BGR images")
        with self._lock:
            if self._terminal is not None:
                raise RuntimeError("Input is closed")
            if self._last_pts is not None and frame.pts < self._last_pts - PTS_RESET_SECONDS:
                self._reset_locked()
            if self._origin is None:
                self._origin = frame.arrival - frame.pts
                self._first_pts = frame.pts
                self._first_index = self._next_index = frame.seq
            if frame.seq < self._next_index:
                self.late_ingest += 1
                return False
            if frame.seq <= self._last_index:
                raise ValueError("Input frame indices must increase")
            self._last_index = frame.seq
            self._last_pts = frame.pts
            if now > self._origin + frame.pts + self._delay:
                self.late_ingest += 1
                return False
            pixels = image.copy() if self._copy_frames else image
            pixels.setflags(write=False)
            stored = _Stored(frame.seq, frame.pts, frame.arrival, pixels)
            self._frames[frame.seq] = stored
            while len(self._frames) > self._max_buffer:
                evicted = next(iter(self._frames))
                del self._frames[evicted]
                self._retro_seqs.discard(evicted)
                self.buffer_evictions += 1
            self._last_frame = stored
            began = time.monotonic()
            self.motion.add(frame.seq, pixels)
            self._stage["motion_submit"].append(time.monotonic() - began)
            return True

    def end(self, last_index: int | None = None) -> None:
        with self._lock:
            self._terminal = self._last_index if last_index is None else last_index

    @property
    def drained(self) -> bool:
        return self._terminal is not None and self._next_index > self._terminal

    def submit_result(self, result: LaneResult) -> None:
        """Record one lane run. ``ok=False`` is kept but never counts as coverage."""
        with self._lock:
            if result.lane not in self._lanes:
                stats = self._lane_stats.setdefault(result.lane, _LaneStats())
                stats.discarded += 1
                return
            stats = self._lane_stats[result.lane]
            if self._origin is not None and result.frame_seq < self._next_index - self._before:
                stats.discarded += 1  # too old to cover any frame still to come
                return
            stats.results += 1
            if not result.ok:
                stats.failures += 1
            line = self._timeline[result.lane]
            line.append(result)
            if len(line) > 512:
                del line[: len(line) - 512]

    def submit_request(self, request: MaskRequest) -> None:
        """Audio-originated request (FULL_FRAME or MOUTH_OF_SPEAKER); applies by PTS range only."""
        if request.kind is MaskKind.REGION:
            raise ValueError("REGION requests must come through a LaneResult")
        with self._lock:
            self._extra[request.request_id] = request

    # ------------------------------------------------------------------ hold

    def hold(
        self,
        source: HoldSource | str,
        active: bool,
        *,
        reason: str | None = None,
        now: float | None = None,
    ) -> str:
        """Assert or release one hold source. Person-triggered sources also hold the recent buffer."""
        now = time.monotonic() if now is None else now
        src = HoldSource(source)
        with self._lock:
            was_active = self.control.is_held(src)
            state = self.control.set_hold(src, active, reason=reason, at=now)
            for window in self.control.take_retro_windows():
                self._apply_retro(window.source, window.t_from, window.t_to)
            if active or was_active:
                self._fence_through = max(self._fence_through, self._last_index)
            return state

    def apply_command(self, command: HoldCommand, now: float | None = None) -> str:
        return self.hold(command.source, command.active, now=now)

    @property
    def hold_active(self) -> bool:
        return self.control.is_held()

    def _apply_retro(self, source: HoldSource, t_from: float, t_to: float) -> None:
        for stored in self._frames.values():
            if stored.arrival >= t_from:
                self._retro_seqs.add(stored.seq)
        if self._origin is not None:
            self._retro_ranges.append(RetroRange(source, t_from - self._origin, t_to - self._origin))

    def pop_retro_ranges(self) -> list[RetroRange]:
        """PTS ranges whose audio must be zeroed because of retroactive holds (drains the list)."""
        with self._lock:
            ranges, self._retro_ranges = self._retro_ranges, []
            return ranges

    def recheck(self, decision: FrameDecision) -> FrameDecision:
        """Hold re-check at the egress writer and encoder hand-off: upgrade to HOLD if one arrived."""
        with self._lock:
            if self.control.state == HOLD and decision.state != HOLD:
                decision.state = HOLD
                decision.reason = self.control.reason
                decision.presentation = "slate"
                decision.masks = ()
                decision.full_frame = False
                decision.mouth_requests = ()
                decision.mouth_quads = ()
                self._fence_through = max(self._fence_through, self._last_index)
            return decision

    # ------------------------------------------------------------------ the tick

    def poll(self, now: float | None = None) -> list[FrameDecision]:
        """Decide every frame whose deadline has passed (at most ``max_poll_frames``)."""
        now = time.monotonic() if now is None else now
        out: list[FrameDecision] = []
        began = time.monotonic()
        with self._lock:
            if self._origin is None:
                return out
            while not self.drained and len(out) < self._max_poll:
                index = self._next_index
                stored = self._frames.get(index)
                pts = stored.pts if stored else self._extrapolate_pts(index)
                deadline = self._origin + pts + self._delay
                if now < deadline:
                    break
                out.append(self._decide(index, pts, stored, deadline, now))
                self._frames.pop(index, None)
                self._retro_seqs.discard(index)
                self._last_emit = (index, pts)
                self._next_index += 1
            oldest = self._next_index - self._before
            for lane, line in self._timeline.items():
                if line and line[0].frame_seq < oldest:
                    self._timeline[lane] = [r for r in line if r.frame_seq >= oldest]
            if self._extra and out:
                horizon = out[-1].pts
                self._extra = {k: r for k, r in self._extra.items() if r.pts_end >= horizon}
        if out:
            self._stage["poll"].append((time.monotonic() - began) / len(out))
        return out

    def _extrapolate_pts(self, index: int) -> float:
        if self._last_emit is not None:
            last_index, last_pts = self._last_emit
            return last_pts + (index - last_index) / self._fps
        return self._first_pts + (index - self._first_index) / self._fps

    def _decide(
        self, index: int, pts: float, stored: _Stored | None, deadline: float, now: float
    ) -> FrameDecision:
        began = time.monotonic()
        cover = self._cover(index, pts, deadline)
        if stored is None:
            cover.ok, cover.reason = False, "ingest-gap"
        self._stage["coverage"].append(time.monotonic() - began)
        self.control.update_coverage(cover.ok, cover.reason, now)
        control_state = self.control.state
        retro = index in self._retro_seqs
        fenced = False
        if control_state == HOLD:
            state, reason = HOLD, self.control.reason
            self._fence_through = max(self._fence_through, self._last_index)
        elif retro:
            state, reason = HOLD, "retro-hold"
        elif index <= self._fence_through:
            state, reason, fenced = HOLD, "hold-fence", True
        else:
            state, reason = control_state, self.control.reason
        if state == LIVE:
            self._protect_since = None
        elif self._protect_since is None:
            self._protect_since = deadline
        protect_age = 0.0 if self._protect_since is None else max(0.0, now - self._protect_since)
        if state == LIVE:
            presentation: Presentation = "frame"
            masks = tuple(mask for _, mask in cover.masks.values())
            full_frame, mouth = cover.full_frame, tuple(cover.mouth.values())
        else:
            presentation = "mean" if state == PROTECT and protect_age < self._protect_fill_s else "slate"
            masks, full_frame, mouth = (), False, ()
        mouth_quads = self._mouth_quads(index, deadline) if mouth and not full_frame else ()
        if stored is not None:
            image = stored.image
            self._last_pixels = image
        else:
            image = (
                self._last_pixels
                if self._last_pixels is not None
                else (self._last_frame.image if self._last_frame is not None else None)
            )
        self.outputs += 1
        if state == LIVE:
            self.live_frames += 1
        elif state == PROTECT:
            self.protect_frames += 1
        else:
            self.hold_frames += 1
        self._emit_lag.append(now - deadline)
        self.reason_counts[f"{state}:{reason}"] += 1
        self._last_ages = cover.ages
        return FrameDecision(
            seq=index,
            pts=pts,
            image=image,
            state=state,  # type: ignore[arg-type]
            reason=reason,
            presentation=presentation,
            masks=masks,
            full_frame=full_frame,
            mouth_requests=mouth,
            deadline=deadline,
            emitted_at=now,
            protect_age=protect_age,
            lane_ages=cover.ages,
            gap=stored is None,
            retro=retro,
            fenced=fenced,
            arrival=stored.arrival if stored else None,
            mouth_quads=mouth_quads,
        )

    def _mouth_quads(self, index: int, deadline: float) -> tuple[Quad, ...]:
        """Resolve the active MOUTH_OF_SPEAKER windows to padded quads on frame ``index`` (lock held)."""
        results = (r for lane in self._lanes for r in self._timeline[lane])
        return resolve_mouth_quads(
            results,
            index,
            deadline,
            (index - self._before, index + self._after),
            self._max_age,
            self.motion.project_many,
        )

    # ------------------------------------------------------------------ coverage

    def _cover(self, index: int, pts: float, deadline: float) -> _Coverage:
        cover = _Coverage(ok=True, reason="covered")
        if not self._lanes:
            cover.ok, cover.reason = False, "no-lanes"
            return cover
        lo, hi = index - self._before, index + self._after
        for lane in self._lanes:
            max_age = self._max_age[lane]
            candidates: list[tuple[int, LaneResult]] = []
            failed = False
            for result in self._timeline[lane]:
                if result.frame_seq < lo or result.frame_seq > hi or result.completed_at > deadline:
                    continue
                if not result.ok:
                    failed = True
                    continue
                candidates.append((abs(result.frame_seq - index), result))
            candidates.sort(key=lambda c: (c[0], -c[1].frame_seq))
            covered_age: int | None = None
            unresolved = stale = False
            for age, result in candidates:
                in_age = age <= max_age
                if not in_age and not any(_covers(r, pts) for r in result.requests):
                    stale = True
                    continue
                projected = self._project(result, index, pts, deadline, take_all=in_age)
                if projected is None:
                    unresolved = True
                    continue
                if in_age and covered_age is None:
                    covered_age = age
                elif not in_age:
                    stale = True
                self._collect(cover, pts, projected)
            cover.ages[lane.value] = covered_age
            if covered_age is None and cover.ok:
                kind = (
                    "unresolved-motion"
                    if unresolved
                    else "lane-error"
                    if failed
                    else "stale"
                    if stale
                    else "missing"
                )
                cover.ok, cover.reason = False, f"{kind}-{lane.value}"
            if covered_age is None:
                self._diagnose(lane, index, deadline)
        for request in self._extra.values():
            if _covers(request, pts):
                self._collect(cover, pts, [(request, ())])
        return cover

    def _diagnose(self, lane: Lane, index: int, deadline: float) -> None:
        """Why a lane did not cover frame ``index``: counters of what the timeline held (lock held)."""
        name = lane.value
        timeline = self._timeline[lane]
        if not timeline:
            self.lane_diag[f"{name}:timeline-empty"] += 1
            return
        on_time = [abs(r.frame_seq - index) for r in timeline if r.ok and r.completed_at <= deadline]
        late = [abs(r.frame_seq - index) for r in timeline if r.ok and r.completed_at > deadline]
        if on_time:
            nearest = min(on_time)
            self.lane_diag[f"{name}:nearest-on-time>{self._max_age[lane]}"] += 1
            self._miss_distance.setdefault(name, deque(maxlen=4096)).append(nearest)
        elif late:
            self.lane_diag[f"{name}:only-late-results"] += 1
        else:
            self.lane_diag[f"{name}:only-failed-results"] += 1

    def _seq_for(self, request: MaskRequest, result: LaneResult) -> int:
        delta = request.frame_pts - result.pts
        if abs(delta) * self._fps < 0.5:
            return result.frame_seq
        return result.frame_seq + round(delta * self._fps)

    def _project(
        self, result: LaneResult, index: int, pts: float, deadline: float, *, take_all: bool
    ) -> list[tuple[MaskRequest, tuple[Quad, ...]]] | None:
        """Project a result's requests to frame ``index``; None if any motion step is unresolved."""
        groups: dict[int, list[MaskRequest]] = {result.frame_seq: []}
        fixed: list[MaskRequest] = []
        for request in result.requests:
            if not take_all and not _covers(request, pts):
                continue
            if request.kind is MaskKind.REGION:
                groups.setdefault(self._seq_for(request, result), []).append(request)
            else:
                fixed.append(request)
        projected_out: list[tuple[MaskRequest, tuple[Quad, ...]]] = []
        for source, requests in groups.items():
            flat = [[list(point) for point in quad.pts] for r in requests for quad in r.quads]
            projected = self.motion.project_many(flat, source, index, deadline)
            if projected is None:
                return None
            cursor = 0
            for request in requests:
                count = len(request.quads)
                quads = tuple(
                    Quad(tuple((float(x), float(y)) for x, y in corners))  # type: ignore[arg-type]
                    for corners in projected[cursor : cursor + count]
                )
                cursor += count
                projected_out.append((request, quads))
        projected_out.extend((request, ()) for request in fixed)
        return projected_out

    @staticmethod
    def _collect(
        cover: _Coverage, pts: float, projected: Sequence[tuple[MaskRequest, tuple[Quad, ...]]]
    ) -> None:
        for request, quads in projected:
            if request.kind is MaskKind.FULL_FRAME:
                cover.full_frame = True
            elif request.kind is MaskKind.MOUTH_OF_SPEAKER:
                if _covers(request, pts):
                    cover.mouth[request.request_id] = request
            else:
                distance = abs(request.frame_pts - pts)
                held = cover.masks.get(request.request_id)
                if held is None or distance < held[0]:
                    cover.masks[request.request_id] = (distance, ProjectedMask(request, quads))

    # ------------------------------------------------------------------ lifecycle and stats

    def reset(self) -> None:
        """Start a new session clock (PTS reset or clock jump). Holds stay; the engine is PROTECT."""
        with self._lock:
            self._reset_locked()

    def _reset_locked(self) -> None:
        self._frames.clear()
        self._retro_seqs.clear()
        self._extra.clear()
        for lane in self._timeline:
            self._timeline[lane] = []
        self._fence_through = -1
        self._reset_state()
        self.resets += 1
        reset = getattr(self.motion, "reset", None)
        if reset is not None:
            reset()
        self.control.update_coverage(False, "session-reset", time.monotonic())

    def lanes_stale(self) -> tuple[str, ...]:
        """Lane values with no covering result at the latest decision (for ``EngineStatus``)."""
        with self._lock:
            ages = self._last_ages
            return tuple(lane.value for lane in self._lanes if ages.get(lane.value) is None)

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "outputs": self.outputs,
                "live": self.live_frames,
                "protect": self.protect_frames,
                "hold": self.hold_frames,
                "late_ingest": self.late_ingest,
                "buffered_frames": len(self._frames),
                "buffer_evictions": self.buffer_evictions,
                "resets": self.resets,
                "stage_seconds": {name: list(values) for name, values in self._stage.items()},
                "emit_lag_seconds": list(self._emit_lag),
                "lanes": {
                    lane.value: {
                        "results": s.results,
                        "failures": s.failures,
                        "discarded": s.discarded,
                    }
                    for lane, s in self._lane_stats.items()
                },
                "reason_counts": dict(self.reason_counts),
                "lane_diag": dict(self.lane_diag),
                "miss_distance": {k: list(v) for k, v in self._miss_distance.items()},
                "motion_worker": getattr(self.motion, "stats", {}),
                "transitions": self.control.transitions,
            }

    def close(self) -> None:
        if self.outputs:
            log.info(
                "coverage summary: outputs=%d live=%d protect=%d hold=%d reasons=%s lane_diag=%s motion_unresolved=%s",
                self.outputs,
                self.live_frames,
                self.protect_frames,
                self.hold_frames,
                dict(self.reason_counts),
                dict(self.lane_diag),
                getattr(self.motion, "stats", {}).get("unresolved"),
            )
        close = getattr(self.motion, "close", None)
        if close is not None:
            close()
