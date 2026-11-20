"""Tracking for the vision lanes (T2.3): in-house ByteTrack, hysteresis on tracks, per-track votes.

Shared helpers: detector packages import from here, never from each other. See ``docs/specs/vision.md``
section 5.4 and decision D-31.
"""

from .bytetrack import ByteTracker, ByteTrackParams, Track, TrackState, iou_matrix
from .hysteresis import (
    CONFIRM_CONF,
    DEFAULT_HOLD_S,
    GC_FACTOR,
    HOLD_S,
    K_CONFIRM,
    Observation,
    TrackedDetection,
    TrackHysteresis,
    TrackVotes,
)
from .tracker import ByteTrackHold

__all__ = [
    "CONFIRM_CONF",
    "DEFAULT_HOLD_S",
    "GC_FACTOR",
    "HOLD_S",
    "K_CONFIRM",
    "ByteTrackHold",
    "ByteTrackParams",
    "ByteTracker",
    "Observation",
    "Track",
    "TrackHysteresis",
    "TrackState",
    "TrackVotes",
    "TrackedDetection",
    "iou_matrix",
]
