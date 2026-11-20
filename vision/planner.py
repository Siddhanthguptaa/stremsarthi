"""``MaskPlanner``: ``Detection``s in, ``MaskRequest``s out, one planner per lane (spec 5.3).

Per lane run, for each tracked detection:
1. drop disabled categories; ``allow=True`` faces yield no request (they go to ``aux_detections`` when
   they carry ``aux_quads``); ``RISKY_TEXT`` yields a request only if its category is enabled;
2. sensitivity floor: minimum confidence is ``1 - sensitivity`` clamped to 0.15-0.85, lowered by 0.15 for
   ``mask_when_unsure`` on cards, QR codes and faces;
3. track (``vision.tracking``: ByteTrack association, K-confirm, hold), then place the quad: a moving track
   is projected with its velocity (a held one for up to ``MAX_PROJECT_S`` = 2 s) and swept over the interval
   the request may be painted on, so the mask follows the object between detections;
4. pad by ``padding_px`` (faces: 10 % of the box per side), clamp to the canvas, take style from config;
5. emit ``MaskRequest(frame_pts, pts_start, pts_end = pts_start + lane_max_age / fps)`` (FAST 0.2 s,
   ACCURATE 0.4 s at the defaults);
6. merge quads of one category and style whose IoU is above 0.5; text boxes on one line merge into one.

A track keeps producing requests for ``hold_s`` after its last sighting (cards and QR 2.0 s, faces 0.8 s,
text 1.0 s), so a mask outlives a missed detection. ``request_id`` is stable per track so the engine
de-duplicates overlapping results by nearest anchor (``frame_pts``).

Placement and ``frame_pts``. A static track (speed under ``MOVING_PX_S``) keeps the exact detected quad and
``frame_pts`` = the last sighting, so the engine can project global camera motion from there (the hook for
global motion stays with the engine). A moving track is placed by its own image-plane velocity, so its quad
is already valid at this run's PTS and ``frame_pts`` is that PTS. The projected or swept quad is the
axis-aligned hull of the positions the object may be at (fail-safe: covers "stopped" and "kept going").

Fallback: ``IouHoldTracker`` (IoU match, K-confirm, hold; idea adapted from PrivaStream ``Hysteresis``,
Apache-2.0, ``vision/LICENSE.privastream``) implements the same ``Tracker`` protocol. A planner built with the
default tracker switches to it for good, with a log line, if ByteTrack raises, so a tracker bug costs
precision instead of failing the lane (which would make the engine PROTECT).

Nothing here keeps pixels or text.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol

import numpy as np

from contracts import (
    CANVAS_H,
    CANVAS_W,
    Category,
    CategoryConfig,
    Config,
    Detection,
    Frame,
    Lane,
    LaneResult,
    MaskKind,
    MaskRequest,
    MaskStyle,
    Quad,
)

from .lanes import Assembler
from .tracking import (
    CONFIRM_CONF,
    DEFAULT_HOLD_S,
    HOLD_S,
    K_CONFIRM,
    ByteTrackHold,
    TrackedDetection,
    iou_matrix,
)

log = logging.getLogger("vision.planner")

UNSURE_CATEGORIES = frozenset({Category.AADHAAR, Category.PAN, Category.UPI_QR, Category.FACE})
TEXT_CATEGORIES = frozenset(
    {
        Category.SIGNBOARD,
        Category.PIN_CODE,
        Category.PHONE_NUMBER,
        Category.UPI_ID,
        Category.EMAIL,
        Category.RISKY_TEXT,
    }
)
IOU_MATCH = 0.3
IOU_MERGE = 0.5
FACE_PAD_FRACTION = 0.10  # per side: the box scaled by 1.2 (spec 5.3 "20 % of box size")
MIN_SPEED_PX_S = 6.0  # below this a track is treated as not moving at all (filters estimate noise)
MOVING_PX_S = 24.0  # at or above this the quad is placed by velocity and anchored at the run's PTS
MAX_SPEED_PX_S = 4000.0  # sanity clamp on a velocity estimate
MAX_PROJECT_S = 2.0  # a held mask is projected forward for at most this long
_CODE_OK = re.compile(r"[^A-Za-z0-9_:.\-/]")
_EPS = 1e-9

__all__ = [
    "CONFIRM_CONF",
    "DEFAULT_HOLD_S",
    "HOLD_S",
    "K_CONFIRM",
    "IouHoldTracker",
    "MaskPlanner",
    "TrackedDetection",
    "Tracker",
    "min_confidence",
    "planner_factory",
]


class Tracker(Protocol):
    """The tracker hook. Return one ``TrackedDetection`` per live track that should be masked now
    (confirmed, and within its hold time). Implementations assign ``track_id`` (never ``None``) and may set
    ``velocity`` (px/s) so the planner can project the mask forward."""

    def update(self, detections: Sequence[Detection], pts: float) -> list[TrackedDetection]: ...

    def reset(self) -> None: ...


@dataclass(slots=True)
class _Track:
    track_id: int
    detection: Detection
    last_seen_pts: float
    hits: int = 1
    confirmed: bool = False


class IouHoldTracker:
    """Fallback tracker: greedy IoU association within a category, K-confirm, hold, GC at 3x hold.
    No motion model (velocity is always zero)."""

    def __init__(
        self,
        categories: Mapping[Category, CategoryConfig],
        *,
        hold_s: Mapping[Category, float] | None = None,
        k_confirm: int = K_CONFIRM,
        iou_match: float = IOU_MATCH,
    ) -> None:
        self._categories = categories
        self._hold = dict(HOLD_S if hold_s is None else hold_s)
        self._k = k_confirm
        self._iou = iou_match
        self._tracks: dict[int, _Track] = {}
        self._next_id = 1
        self._last_pts = -float("inf")

    def hold_for(self, category: Category) -> float:
        return self._hold.get(category, DEFAULT_HOLD_S)

    def _confirm_now(self, det: Detection) -> bool:
        cfg = self._categories.get(det.category)
        return det.confidence >= CONFIRM_CONF or bool(cfg is not None and cfg.mask_when_unsure)

    def reset(self) -> None:
        self._tracks.clear()

    def update(self, detections: Sequence[Detection], pts: float) -> list[TrackedDetection]:
        if pts < self._last_pts - 1.0:  # the source clock restarted: old tracks are meaningless
            self._tracks.clear()
        self._last_pts = pts
        free = list(range(len(detections)))
        pairs: list[tuple[float, int, int]] = []
        for tid, tr in self._tracks.items():
            for j in free:
                d = detections[j]
                if d.category is tr.detection.category:
                    iou = tr.detection.quad.iou(d.quad)
                    if iou >= self._iou:
                        pairs.append((iou, tid, j))
        used_t: set[int] = set()
        used_d: set[int] = set()
        for _iou, tid, j in sorted(pairs, reverse=True):
            if tid in used_t or j in used_d:
                continue
            used_t.add(tid)
            used_d.add(j)
            tr = self._tracks[tid]
            tr.detection = replace(detections[j], track_id=tid)
            tr.last_seen_pts = pts
            tr.hits += 1
            if tr.hits >= self._k or self._confirm_now(detections[j]):
                tr.confirmed = True
        for j in free:
            if j in used_d:
                continue
            tid = self._next_id
            self._next_id += 1
            det = replace(detections[j], track_id=tid)
            self._tracks[tid] = _Track(tid, det, pts, 1, self._confirm_now(det))
        out: list[TrackedDetection] = []
        for tid, tr in list(self._tracks.items()):
            age = pts - tr.last_seen_pts
            hold = self.hold_for(tr.detection.category)
            if age > 3.0 * hold:
                del self._tracks[tid]
                continue
            if tr.confirmed and age <= hold + 1e-9:
                out.append(TrackedDetection(tr.detection, tr.last_seen_pts, age > 1e-9, tid))
        return out


# ---------------------------------------------------------------------------- planner
def min_confidence(cfg: CategoryConfig, category: Category) -> float:
    floor = min(0.85, max(0.15, 1.0 - cfg.sensitivity))
    if cfg.mask_when_unsure and category in UNSURE_CATEGORIES:
        floor = max(0.0, floor - 0.15)
    return floor


def _pad_px(quad: Quad, category: Category, cfg: CategoryConfig) -> float:
    if category is Category.FACE:
        x1, y1, x2, y2 = quad.bbox()
        return FACE_PAD_FRACTION * max(x2 - x1, y2 - y1)
    return float(cfg.padding_px)


def _code(text: str) -> str:
    return _CODE_OK.sub("_", text)[:64] or "detected"


@dataclass(slots=True)
class _Item:
    tracked: TrackedDetection
    quad: Quad
    style: MaskStyle
    anchor: float  # the PTS ``quad`` is valid at (the engine projects global motion from here)
    members: list[TrackedDetection] = field(default_factory=list)


def _union_quad(quads: Sequence[Quad]) -> Quad:
    xs1, ys1, xs2, ys2 = zip(*(q.bbox() for q in quads), strict=True)
    return Quad.from_xyxy(min(xs1), min(ys1), max(xs2), max(ys2))


def _line_links(quads: np.ndarray, max_gap_h: float = 2.0, max_perp_h: float = 0.6) -> np.ndarray:
    """Pairs (i < j) of text boxes ((n, 4, 2) clockwise quads) that sit on one text line.

    The same rule as vision.ocr.layout.group_lines (directions within 12 degrees unless both boxes are
    short and squarish, centres within max_perp_h heights across the line, gap along it at most
    max_gap_h heights), vectorised: a 20-box signboard costs about 0.1 ms instead of 3 ms.
    """
    e1 = quads[:, 1] - quads[:, 0]
    e2 = quads[:, 3] - quads[:, 0]
    long_edge = np.where((np.hypot(*e1.T) >= np.hypot(*e2.T))[:, None], e1, e2)
    norm = np.maximum(np.hypot(*long_edge.T), 1e-9)
    u = long_edge / norm[:, None]
    u = np.where((u[:, 0] < 0)[:, None], -u, u)
    side_a = (np.hypot(*(quads[:, 0] - quads[:, 1]).T) + np.hypot(*(quads[:, 3] - quads[:, 2]).T)) / 2.0
    side_b = (np.hypot(*(quads[:, 0] - quads[:, 3]).T) + np.hypot(*(quads[:, 1] - quads[:, 2]).T)) / 2.0
    length, height = np.maximum(side_a, side_b), np.minimum(side_a, side_b)
    h = (height[:, None] + height[None, :]) / 2.0
    angle = np.degrees(np.arccos(np.clip(np.abs(u @ u.T), 0.0, 1.0)))
    ratio = length / np.maximum(height, 1e-6)
    squarish = np.minimum(ratio[:, None], ratio[None, :]) <= 1.5
    centre = quads.mean(axis=1)
    d = centre[None, :, :] - centre[:, None, :]  # d[i, j] = centre_j - centre_i
    along = np.abs((d * u[:, None, :]).sum(axis=2))
    perp = np.abs(d[..., 0] * u[:, None, 1] - d[..., 1] * u[:, None, 0])
    ok = (h > 0) & ((angle <= 12.0) | squarish) & (perp <= max_perp_h * h)
    ok &= along - (length[:, None] + length[None, :]) / 2.0 <= max_gap_h * h
    return np.triu(ok, 1)


def _merge_groups(items: list[_Item]) -> list[list[_Item]]:
    """Group items of one category and style: IoU above 0.5 merges; text boxes on one line merge.

    One IoU matrix over all items and one vectorised line test over the text items (cross-key pairs masked out).
    """
    n = len(items)
    if n <= 1:
        return [items] if items else []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    key_ids: dict[tuple[Category, MaskStyle], int] = {}
    kid = np.array(
        [key_ids.setdefault((it.tracked.detection.category, it.style), len(key_ids)) for it in items]
    )
    boxes = np.array([it.quad.bbox() for it in items], dtype=np.float64)
    same = kid[:, None] == kid[None, :]
    np.fill_diagonal(same, False)
    for i, j in np.argwhere(np.triu((iou_matrix(boxes, boxes) > IOU_MERGE) & same, 1)).tolist():
        parent[find(i)] = find(j)
    text = np.array([it.tracked.detection.category in TEXT_CATEGORIES for it in items])
    idx = np.flatnonzero(text)
    if len(idx) > 1:
        links = _line_links(np.array([items[i].quad.pts for i in idx], dtype=np.float64))
        for a, b in np.argwhere(links & same[np.ix_(idx, idx)]).tolist():
            parent[find(int(idx[a]))] = find(int(idx[b]))
    groups: dict[int, list[_Item]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())


def _pad_clamp(quad: Quad, px: float) -> Quad:
    """``quad.padded(px).clamped()`` with one ``Quad`` built instead of three."""
    cx, cy = quad.center()
    return Quad(
        tuple(  # type: ignore[arg-type]
            (
                min(max(x + (px if x >= cx else -px), 0.0), float(CANVAS_W)),
                min(max(y + (px if y >= cy else -px), 0.0), float(CANVAS_H)),
            )
            for x, y in quad.pts
        )
    )


def sweep_quad(quad: Quad, vx: float, vy: float, t_lo: float, t_hi: float) -> Quad:
    """Axis-aligned hull of ``quad`` translated by ``v * t`` for every ``t`` in ``[t_lo, t_hi]``."""
    x1, y1, x2, y2 = quad.bbox()
    xa, xb = vx * t_lo, vx * t_hi
    ya, yb = vy * t_lo, vy * t_hi
    return Quad.from_xyxy(x1 + min(xa, xb), y1 + min(ya, yb), x2 + max(xa, xb), y2 + max(ya, yb))


def lane_rate_hz(lane: Lane, config: Config) -> float:
    """Nominal runs per second of a lane: ``fps / stride`` (FAST), ``accurate_hz`` or 1 Hz (SLOW)."""
    if lane is Lane.FAST:
        return float(config.fps) / config.detect.stride
    if lane is Lane.ACCURATE:
        return float(config.detect.accurate_hz)
    return 1.0


class MaskPlanner:
    """Callable with the ``vision.lanes.Assembler`` signature: ``planner(frame, lane, detections)``."""

    def __init__(
        self,
        lane: Lane,
        config: Config,
        *,
        tracker: Tracker | Literal["bytetrack", "iou"] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lane = lane
        self.config = config
        self._clock = clock
        self.rate_hz = lane_rate_hz(lane, config)
        self._can_fall_back = False
        self.fell_back = False
        if tracker is None or tracker == "bytetrack":
            self.tracker: Tracker = ByteTrackHold(config.categories, frame_rate=self.rate_hz)
            self._can_fall_back = True
        elif tracker == "iou":
            self.tracker = IouHoldTracker(config.categories)
        else:
            self.tracker = tracker
        age = {
            Lane.FAST: config.engine.lane_max_age.fast,
            Lane.ACCURATE: config.engine.lane_max_age.accurate,
            Lane.SLOW: config.engine.lane_max_age.slow,
        }[lane]
        self.valid_for_s = age / float(config.fps)
        # half a detection interval (the engine paints a frame from the nearest result, which is at most
        # that far away) plus one frame of slack
        self.sweep_s = 0.5 / self.rate_hz + 1.0 / float(config.fps)

    # -------------------------------------------------------------------- assembler
    def __call__(
        self, frame: Frame, lane: Lane, detections: tuple[Detection, ...]
    ) -> tuple[tuple[MaskRequest, ...], tuple[Detection, ...]]:
        return self.plan(frame.pts, detections)

    def _track(self, usable: list[Detection], pts: float) -> list[TrackedDetection]:
        try:
            return self.tracker.update(usable, pts)
        except Exception as exc:
            if not self._can_fall_back:
                raise
            log.error(
                "ByteTrack failed (%s); lane %s falls back to the IoU hold tracker",
                type(exc).__name__,
                self.lane.value,
            )
            self.tracker = IouHoldTracker(self.config.categories)
            self._can_fall_back = False
            self.fell_back = True
            return self.tracker.update(usable, pts)

    def place(self, td: TrackedDetection, pts: float) -> tuple[Quad, float]:
        """The quad to mask for a tracked detection at run time ``pts`` and the PTS it is valid at."""
        base = td.detection.quad
        vx, vy = td.velocity
        speed = math.hypot(vx, vy)
        if not (speed >= MIN_SPEED_PX_S):  # also False for NaN
            return base, td.last_seen_pts
        if speed > MAX_SPEED_PX_S:
            vx, vy = vx * MAX_SPEED_PX_S / speed, vy * MAX_SPEED_PX_S / speed
            speed = MAX_SPEED_PX_S
        age = max(0.0, pts - td.last_seen_pts)
        if age > _EPS:
            # not seen on this run: it is somewhere between where it was last seen and where its velocity
            # puts it now (plus the sweep margin), so cover that whole span
            quad = sweep_quad(base, vx, vy, 0.0, min(age, MAX_PROJECT_S) + self.sweep_s)
        else:
            quad = sweep_quad(base, vx, vy, -self.sweep_s, self.sweep_s)
        return quad, (pts if speed >= MOVING_PX_S else td.last_seen_pts)

    def plan(
        self, pts: float, detections: Sequence[Detection]
    ) -> tuple[tuple[MaskRequest, ...], tuple[Detection, ...]]:
        cats = self.config.categories
        aux = tuple(d for d in detections if d.allow and d.aux_quads)
        usable: list[Detection] = []
        for d in detections:
            cfg = cats.get(d.category)
            if d.allow or cfg is None or not cfg.enabled:
                continue
            if d.confidence < min_confidence(cfg, d.category):
                continue
            usable.append(d)
        tracked = self._track(usable, pts)
        items: list[_Item] = []
        for td in tracked:
            category = td.detection.category
            cfg = cats.get(category)
            if cfg is None or not cfg.enabled:  # config changed while the track was held
                continue
            quad, anchor = self.place(td, pts)
            items.append(_Item(td, _pad_clamp(quad, _pad_px(quad, category, cfg)), cfg.style, anchor))
        now = self._clock()
        requests = [self._request(pts, now, g) for g in _merge_groups(items)]
        requests.sort(key=lambda r: r.request_id)
        return tuple(requests), aux

    def _request(self, pts: float, now: float, group: list[_Item]) -> MaskRequest:
        lead = max(group, key=lambda it: it.tracked.detection.confidence)
        det = lead.tracked.detection
        category, style = det.category, lead.style
        quad = lead.quad if len(group) == 1 else _union_quad([it.quad for it in group])
        anchor = max(it.anchor for it in group)
        held = all(it.tracked.held for it in group)
        reason = _code(str(det.extra.get("reason", "detected")))
        if held:
            reason = _code(f"{reason}:held")
        track_id = min(it.tracked.track_id or it.tracked.detection.track_id or 0 for it in group) or None
        return MaskRequest(
            request_id=f"{det.detector}:{category.value}:{self.lane.value}:{track_id or 0}",
            source=det.detector,
            category=category,
            kind=MaskKind.REGION,
            quads=(quad,),
            frame_pts=anchor,
            pts_start=max(pts, anchor),
            pts_end=max(pts, anchor) + self.valid_for_s,
            style=style,
            confidence=max(it.tracked.detection.confidence for it in group),
            reason=reason,
            completed_at=now,
            track_id=track_id,
        )

    # -------------------------------------------------------------------- LaneResult
    def lane_result(
        self, frame: Frame, detections: Sequence[Detection], completed_at: float | None = None
    ) -> LaneResult:
        """Assemble the ``LaneResult`` for one run: ``ok=True`` with no requests is the heartbeat."""
        requests, aux = self.plan(frame.pts, detections)
        return LaneResult(
            self.lane,
            frame.seq,
            frame.pts,
            self._clock() if completed_at is None else completed_at,
            ok=True,
            requests=requests,
            aux_detections=aux,
        )

    @property
    def live_tracks(self) -> int:
        """Tracks the tracker still remembers (0 for trackers that do not report it)."""
        return int(getattr(self.tracker, "live_tracks", 0))


def planner_factory(lane: Lane, config: Config) -> Assembler:
    """``VisionHost(assembler_factory=planner_factory)``: one planner (and tracker) per lane."""
    return MaskPlanner(lane, config)
