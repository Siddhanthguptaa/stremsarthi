"""In-house ByteTrack (D-31): numpy + ``scipy.optimize.linear_sum_assignment``, no ``supervision``, no ``lap``.

Algorithm (Zhang et al., "ByteTrack: Multi-Object Tracking by Associating Every Detection Box", ECCV 2022;
the reference implementation is MIT). Per update:

1. Every live track is advanced by a Kalman filter to the new timestamp.
2. High-score detections (``score >= activation_threshold``) are matched to TRACKED and LOST tracks by IoU
   (cost ``1 - IoU * score`` when ``fuse_score``, gate ``match_threshold``), Hungarian assignment.
3. Left-over TRACKED tracks are matched to low-score detections (plain IoU, gate ``low_match_threshold``).
4. NEW tracks (seen once) are matched to the high detections that are left (gate ``unconfirmed_threshold``).
5. Unmatched TRACKED and NEW tracks become LOST; a LOST track older than ``lost_track_buffer / frame_rate``
   seconds is REMOVED. Detections that are still unmatched and score at least ``new_track_threshold`` start
   NEW tracks.

Differences from the reference, all on purpose:
* The Kalman filter state is ``(cx, cy, w, h)`` plus velocities (not aspect ratio). Because the process and
  measurement noise are diagonal, the 8-D filter factorises into four independent 2x2 filters, which is
  what is implemented here (vectorised over tracks, no matrix inversion, exact).
* Time is the source PTS, not a frame counter: lanes run at 10 Hz (FAST) and 4 Hz (ACCURATE) with jitter, so
  the filter advances by ``(pts - last_pts) * frame_rate`` nominal steps and the lost buffer is in seconds.
  Velocities are reported in pixels per second.
* A NEW track that is not matched on the next run becomes LOST (not REMOVED), so an object that is seen,
  missed once and seen again keeps its id (fail-safe bias; the hysteresis layer depends on stable ids).
* Optional ``labels`` gate the association (a detection only matches tracks with the same label), so two
  categories never share a track.
* The original detection payload (for us the ``Detection`` with its exact ``Quad``) is kept on the track;
  the Kalman box is only used for association and velocity.

Deterministic: no randomness, Hungarian ties break by index, ids count up from 1.
"""

from __future__ import annotations

import enum
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

_MIN_SIDE = 1.0  # px: a degenerate box would break the IoU and the noise scale
_NO_MATCH = 2.0  # cost above every gate
_MAX_STEPS = 60.0  # never advance the filter by more than this many nominal steps in one update
_BACKJUMP_S = 1.0  # pts jumping back by more than this means the source clock restarted
# rows of the per-track filter state
_P, _V, _PPP, _PPV, _PVV = range(5)


class TrackState(enum.IntEnum):
    NEW = 0  # created from one detection, not matched again yet
    TRACKED = 1
    LOST = 2  # not matched on the last update, still inside the lost buffer
    REMOVED = 3


@dataclass(frozen=True, slots=True)
class ByteTrackParams:
    """Thresholds. Defaults follow docs/specs/vision.md 5.4 (supervision's defaults for the first three)."""

    activation_threshold: float = 0.25  # high/low score split
    lost_track_buffer: int = 15  # runs at ``frame_rate`` (1.5 s at FAST rate)
    match_threshold: float = 0.8  # max association cost for high-score detections
    frame_rate: float = 10.0  # nominal runs per second of this lane
    low_threshold: float = 0.1  # detections below this are ignored by the tracker
    low_match_threshold: float = 0.5
    unconfirmed_threshold: float = 0.7
    new_track_threshold: float | None = None  # default: activation_threshold
    fuse_score: bool = True
    std_pos: float = 1.0 / 20.0  # measurement and process noise weights (per nominal step at 30 fps)
    std_vel: float = 1.0 / 160.0
    init_vel_std: float = 1.0  # prior velocity std, fraction of the box side per step: locks onto speed fast

    def __post_init__(self) -> None:
        if self.frame_rate <= 0:
            raise ValueError("frame_rate must be > 0")
        if self.lost_track_buffer < 0:
            raise ValueError("lost_track_buffer must be >= 0")


@dataclass(eq=False, slots=True)
class Track:
    track_id: int
    label: Hashable | None
    state: TrackState
    kf: np.ndarray  # (5, 4): rows p, v, Ppp, Ppv, Pvv; columns cx, cy, w, h
    kf_pts: float  # the time ``kf`` refers to
    start_pts: float
    last_hit_pts: float  # last time a detection was matched to this track
    score: float
    hits: int = 1
    observed: bool = True  # matched (or created) in the latest update
    payload: Any = None  # the detection matched last (kept untouched: exact quad for masks)
    velocity_px_s: tuple[float, float] = (0.0, 0.0)

    @property
    def box_xyxy(self) -> tuple[float, float, float, float]:
        cx, cy, w, h = (float(x) for x in self.kf[_P])
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    @property
    def center(self) -> tuple[float, float]:
        return float(self.kf[_P, 0]), float(self.kf[_P, 1])


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of two ``(n, 4)`` / ``(m, 4)`` arrays of ``x1, y1, x2, y2``."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    iw = np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0])
    ih = np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1])
    inter = np.clip(iw, 0.0, None) * np.clip(ih, 0.0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.where(union > 0, union, 1.0), 0.0)


def _to_cxcywh(box: np.ndarray) -> np.ndarray:
    w = max(box[2] - box[0], _MIN_SIDE)
    h = max(box[3] - box[1], _MIN_SIDE)
    return np.array([(box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0, w, h])


class ByteTracker:
    """One tracker per lane. ``update`` is not thread-safe (a lane worker owns it)."""

    def __init__(self, params: ByteTrackParams | None = None) -> None:
        self.params = params or ByteTrackParams()
        p = self.params
        self.max_time_lost_s = p.lost_track_buffer / p.frame_rate
        self._new_thr = p.activation_threshold if p.new_track_threshold is None else p.new_track_threshold
        boost = max(1.0, 30.0 / p.frame_rate)  # slower lanes move more per step
        self._q_pos = p.std_pos * boost
        self._q_vel = p.std_vel * boost
        self._tracks: dict[int, Track] = {}
        self._next_id = 1
        self._last_pts = -float("inf")
        self._label_ids: dict[Hashable | None, int] = {}

    # ------------------------------------------------------------------ public
    @property
    def tracks(self) -> list[Track]:
        """Live tracks (NEW, TRACKED, LOST) in creation order."""
        return list(self._tracks.values())

    def reset(self) -> None:
        self._tracks.clear()
        self._last_pts = -float("inf")

    def update(
        self,
        boxes: np.ndarray | Sequence[Sequence[float]],
        scores: np.ndarray | Sequence[float],
        pts: float,
        labels: Sequence[Hashable | None] | None = None,
        payloads: Sequence[Any] | None = None,
    ) -> list[Track]:
        """Advance to ``pts`` and associate. ``boxes`` are ``x1, y1, x2, y2`` pixels.

        Returns the tracks that were matched or created by this call (``observed=True``), in track-id order.
        Unmatched live tracks stay available in ``tracks`` (state LOST)."""
        det = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
        sc = np.asarray(scores, dtype=np.float64).reshape(-1)
        n = len(det)
        if len(sc) != n or any(x is not None and len(x) != n for x in (labels, payloads)):
            raise ValueError("boxes, scores, labels and payloads must have the same length")
        lab: list[Hashable | None] = list(labels) if labels is not None else [None] * n
        pay: list[Any] = list(payloads) if payloads is not None else [None] * n
        if pts < self._last_pts - _BACKJUMP_S:  # the source clock restarted: old tracks are meaningless
            self._tracks.clear()
        self._last_pts = max(self._last_pts, pts)
        p = self.params
        for t in self._tracks.values():
            t.observed = False
        self._predict(pts)

        high = [i for i in range(n) if sc[i] >= p.activation_threshold]
        low = [i for i in range(n) if p.low_threshold <= sc[i] < p.activation_threshold]
        pool = [t for t in self._tracks.values() if t.state in (TrackState.TRACKED, TrackState.LOST)]
        fresh = [t for t in self._tracks.values() if t.state is TrackState.NEW]

        matched1, pool_left, high_left = self._associate(
            pool, high, det, sc, lab, p.match_threshold, p.fuse_score
        )
        self._apply_many(matched1, det, sc, pay, pts)

        tracked_left = [t for t in pool_left if t.state is TrackState.TRACKED]
        matched2, tracked_left, low_left = self._associate(
            tracked_left, low, det, sc, lab, p.low_match_threshold, False
        )
        self._apply_many(matched2, det, sc, pay, pts)

        matched3, fresh_left, high_left = self._associate(
            fresh, high_left, det, sc, lab, p.unconfirmed_threshold, p.fuse_score
        )
        self._apply_many(matched3, det, sc, pay, pts)

        for t in tracked_left + fresh_left:
            t.state = TrackState.LOST
        for t in list(self._tracks.values()):
            if t.state is TrackState.LOST and pts - t.last_hit_pts > self.max_time_lost_s + 1e-9:
                t.state = TrackState.REMOVED
                del self._tracks[t.track_id]

        for i in sorted(high_left + low_left):
            if sc[i] >= self._new_thr:
                self._start(det[i], sc[i], lab[i], pay[i], pts)
        return [t for t in self._tracks.values() if t.observed]

    # ------------------------------------------------------------------ association
    def _associate(
        self,
        tracks: list[Track],
        det_idx: list[int],
        det: np.ndarray,
        sc: np.ndarray,
        lab: list[Hashable | None],
        gate: float,
        fuse: bool,
    ) -> tuple[list[tuple[Track, int]], list[Track], list[int]]:
        if not tracks or not det_idx:
            return [], list(tracks), list(det_idx)
        kf = np.stack([t.kf[_P] for t in tracks])
        tb = np.column_stack(
            (
                kf[:, 0] - kf[:, 2] / 2,
                kf[:, 1] - kf[:, 3] / 2,
                kf[:, 0] + kf[:, 2] / 2,
                kf[:, 1] + kf[:, 3] / 2,
            )
        )
        ious = iou_matrix(tb, det[det_idx])
        cost = 1.0 - (ious * sc[det_idx][None, :] if fuse else ious)
        if any(t.label is not None for t in tracks) or any(lab[j] is not None for j in det_idx):
            tl = np.array([self._label_id(t.label) for t in tracks])
            dl = np.array([self._label_id(lab[j]) for j in det_idx])
            cost[tl[:, None] != dl[None, :]] = _NO_MATCH
        rows, cols = linear_sum_assignment(cost)
        matched: list[tuple[Track, int]] = []
        used_t: set[int] = set()
        used_d: set[int] = set()
        for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
            if cost[r, c] <= gate:
                matched.append((tracks[r], det_idx[c]))
                used_t.add(r)
                used_d.add(c)
        return (
            matched,
            [t for a, t in enumerate(tracks) if a not in used_t],
            [j for b, j in enumerate(det_idx) if b not in used_d],
        )

    def _label_id(self, label: Hashable | None) -> int:
        return self._label_ids.setdefault(label, len(self._label_ids))

    # ------------------------------------------------------------------ Kalman (4 independent 2x2 filters)
    @staticmethod
    def _scale(p: np.ndarray) -> np.ndarray:
        """Noise scale per column (cx, cy, w, h): the box side along that axis."""
        return np.maximum(p[..., [2, 3, 2, 3]], _MIN_SIDE)

    def _predict(self, pts: float) -> None:
        tracks = list(self._tracks.values())
        if not tracks:
            return
        kf = np.stack([t.kf for t in tracks])  # (N, 5, 4)
        steps = np.array(
            [min(_MAX_STEPS, max(0.0, pts - t.kf_pts) * self.params.frame_rate) for t in tracks]
        )[:, None]
        lost = np.array([t.state is TrackState.LOST for t in tracks])
        kf[lost, _V, 2:] = 0.0  # a lost box keeps its size (ByteTrack zeroes the size velocity)
        p, v, ppp, ppv, pvv = (kf[:, i] for i in range(5))
        scale = self._scale(p)
        qp = (self._q_pos * scale) ** 2 * steps
        qv = (self._q_vel * scale) ** 2 * steps
        out = np.empty_like(kf)
        out[:, _P] = p + steps * v
        out[:, _V] = v
        out[:, _PPP] = ppp + 2.0 * steps * ppv + steps * steps * pvv + qp
        out[:, _PPV] = ppv + steps * pvv
        out[:, _PVV] = pvv + qv
        np.maximum(out[:, _P, 2:], _MIN_SIDE, out=out[:, _P, 2:])
        for t, row in zip(tracks, out, strict=True):
            t.kf = row
            t.kf_pts = max(t.kf_pts, pts)

    def _apply_many(
        self, pairs: list[tuple[Track, int]], det: np.ndarray, sc: np.ndarray, pay: list[Any], pts: float
    ) -> None:
        """Kalman measurement update for matched (track, detection index) pairs, vectorised over the pairs."""
        if not pairs:
            return
        tracks = [t for t, _ in pairs]
        idx = [i for _, i in pairs]
        kf = np.stack([t.kf for t in tracks])  # (M, 5, 4)
        b = det[idx]
        z = np.column_stack(
            (
                (b[:, 0] + b[:, 2]) / 2.0,
                (b[:, 1] + b[:, 3]) / 2.0,
                np.maximum(b[:, 2] - b[:, 0], _MIN_SIDE),
                np.maximum(b[:, 3] - b[:, 1], _MIN_SIDE),
            )
        )
        r = (self.params.std_pos * self._scale(kf[:, _P])) ** 2
        s = kf[:, _PPP] + r
        kp = kf[:, _PPP] / s
        kv = kf[:, _PPV] / s
        new = np.empty_like(kf)
        new[:, _P] = kf[:, _P] + kp * (z - kf[:, _P])
        new[:, _V] = kf[:, _V] + kv * (z - kf[:, _P])
        new[:, _PPP] = (1.0 - kp) * kf[:, _PPP]
        new[:, _PPV] = (1.0 - kp) * kf[:, _PPV]
        new[:, _PVV] = kf[:, _PVV] - kv * kf[:, _PPV]
        vel = new[:, _V, :2] * self.params.frame_rate
        for k, (t, i) in enumerate(pairs):
            t.kf = new[k]
            t.kf_pts = max(t.kf_pts, pts)
            t.state = TrackState.TRACKED
            t.last_hit_pts = pts
            t.score = float(sc[i])
            t.hits += 1
            t.observed = True
            t.payload = pay[i]
            t.velocity_px_s = (float(vel[k, 0]), float(vel[k, 1]))

    def _start(
        self, box: np.ndarray, score: float, label: Hashable | None, payload: Any, pts: float
    ) -> Track:
        z = _to_cxcywh(box)
        scale = self._scale(z)
        kf = np.zeros((5, 4))
        kf[_P] = z
        kf[_PPP] = (2.0 * self.params.std_pos * scale) ** 2
        kf[_PVV] = (self.params.init_vel_std * scale) ** 2
        t = Track(
            track_id=self._next_id,
            label=label,
            state=TrackState.NEW,
            kf=kf,
            kf_pts=pts,
            start_pts=pts,
            last_hit_pts=pts,
            score=float(score),
            payload=payload,
        )
        self._next_id += 1
        self._tracks[t.track_id] = t
        return t
