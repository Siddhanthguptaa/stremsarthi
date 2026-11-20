"""``ByteTrackHold``: ByteTrack association plus track hysteresis, behind the planner's ``Tracker`` protocol.

``update(detections, pts)`` returns one ``TrackedDetection`` per confirmed track that must be masked now:
tracks matched on this run (``held=False``) and tracks that were not (``held=True``, within the category's
hold time), each with the exact ``Quad`` of its last matched detection and its velocity in px/s.

The planner has already applied its per-category confidence floor, so this tracker starts tracks from every
detection it is given (``low_threshold`` and ``new_track_threshold`` are 0 here; the standalone
``ByteTracker`` keeps the ByteTrack defaults). The lost-track buffer is at least the longest hold, so a
track is never forgotten while it is still being held.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

import numpy as np

from contracts import Category, CategoryConfig, Detection

from .bytetrack import ByteTracker, ByteTrackParams
from .hysteresis import (
    CONFIRM_CONF,
    DEFAULT_HOLD_S,
    GC_FACTOR,
    HOLD_S,
    K_CONFIRM,
    Observation,
    TrackedDetection,
    TrackHysteresis,
)

LOST_BUFFER_RUNS = 15  # spec 5.4


class ByteTrackHold:
    def __init__(
        self,
        categories: Mapping[Category, CategoryConfig],
        *,
        frame_rate: float,
        hold_s: Mapping[Category, float] | None = None,
        default_hold_s: float = DEFAULT_HOLD_S,
        k_confirm: int = K_CONFIRM,
        confirm_conf: float = CONFIRM_CONF,
        gc_factor: float = GC_FACTOR,
        params: ByteTrackParams | None = None,
    ) -> None:
        self.hysteresis = TrackHysteresis(
            categories,
            hold_s=HOLD_S if hold_s is None else hold_s,
            default_hold_s=default_hold_s,
            k_confirm=k_confirm,
            confirm_conf=confirm_conf,
            gc_factor=gc_factor,
        )
        lost = max(LOST_BUFFER_RUNS, math.ceil(self.hysteresis.max_hold_s * frame_rate))
        base = params or ByteTrackParams()
        self.tracker = ByteTracker(
            replace(
                base,
                frame_rate=frame_rate,
                lost_track_buffer=max(base.lost_track_buffer, lost),
                low_threshold=0.0,
                new_track_threshold=0.0,
            )
        )

    def hold_for(self, category: Category) -> float:
        return self.hysteresis.hold_for(category)

    def reset(self) -> None:
        self.tracker.reset()
        self.hysteresis.reset()

    @property
    def live_tracks(self) -> int:
        return len(self.tracker.tracks)

    def update(self, detections: Sequence[Detection], pts: float) -> list[TrackedDetection]:
        if detections:
            boxes = np.array([d.quad.bbox() for d in detections], dtype=np.float64)
            scores = np.array([d.confidence for d in detections], dtype=np.float64)
        else:
            boxes = np.zeros((0, 4))
            scores = np.zeros(0)
        observed = self.tracker.update(
            boxes, scores, pts, labels=[d.category for d in detections], payloads=list(detections)
        )
        return self.hysteresis.update(
            [Observation(t.track_id, t.payload, t.velocity_px_s) for t in observed], pts
        )
