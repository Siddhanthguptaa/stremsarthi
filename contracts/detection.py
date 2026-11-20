"""Detections, mask requests and mute spans: what the media modules exchange."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from .enums import Category, Lane, MaskKind, MaskStyle
from .geometry import Quad
from .privacy import validate_instance


def _unit(name: str, value: float) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be within 0..1, got {value}")


@dataclass(frozen=True, slots=True)
class Detection:
    detector: str  # plugin id
    category: Category
    quad: Quad
    confidence: float
    frame_seq: int
    pts: float
    completed_at: float  # time.monotonic() when inference finished (deadline logic)
    track_id: int | None = None
    allow: bool = False  # allowlisted face: must NOT be masked
    aux_quads: Mapping[str, Quad] = field(default_factory=dict)  # e.g. {"mouth": Quad}
    extra: Mapping[str, float | int | bool | str] = field(default_factory=dict)  # codes/scores only

    def __post_init__(self) -> None:
        _unit("Detection.confidence", self.confidence)
        if not (math.isfinite(self.pts) and math.isfinite(self.completed_at)):
            raise ValueError("Detection times must be finite")
        if self.allow and self.category is not Category.FACE:
            raise ValueError("Detection.allow is only valid for Category.FACE")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class MaskRequest:
    request_id: str
    source: str  # detector id, "audio" or "hold"
    category: Category
    kind: MaskKind
    quads: tuple[Quad, ...]  # REGION only; anchored at frame_pts
    frame_pts: float  # PTS the quads were computed on (engine projects motion from here)
    pts_start: float
    pts_end: float  # frames with pts in [pts_start, pts_end] receive the mask
    style: MaskStyle
    confidence: float
    reason: str  # machine code, e.g. "verhoeff_ok"
    completed_at: float
    track_id: int | None = None

    def __post_init__(self) -> None:
        _unit("MaskRequest.confidence", self.confidence)
        if self.pts_end < self.pts_start:
            raise ValueError("MaskRequest.pts_end must be >= pts_start")
        if self.kind is MaskKind.REGION and not self.quads:
            raise ValueError("MaskRequest(kind=REGION) needs at least one quad")
        if self.kind is not MaskKind.REGION and self.quads:
            raise ValueError("quads are only allowed for kind=REGION")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class MuteSpan:
    span_id: str
    pts_start: float
    pts_end: float  # already padded by the audio module
    category: Category
    granularity: Literal["word", "segment", "failsafe"]
    confidence: float
    reason: str  # code, never transcript text
    completed_at: float
    mode: Literal["silence", "bleep"] = "silence"

    def __post_init__(self) -> None:
        _unit("MuteSpan.confidence", self.confidence)
        if self.pts_end < self.pts_start:
            raise ValueError("MuteSpan.pts_end must be >= pts_start")
        if self.granularity not in ("word", "segment", "failsafe"):
            raise ValueError("invalid MuteSpan.granularity")
        if self.mode not in ("silence", "bleep"):
            raise ValueError("invalid MuteSpan.mode")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class LaneResult:
    """What a vision lane hands the engine after one run. In-process only (never on the wire).

    An empty ``requests`` with ``ok=True`` is a valid "ran and found nothing" heartbeat: the
    engine's fail-safe needs it to tell "nothing there" from "lane is late". ``aux_detections``
    carries allowlisted faces (for mouth quads) and nothing else.
    """

    lane: Lane
    frame_seq: int
    pts: float
    completed_at: float
    ok: bool
    requests: tuple[MaskRequest, ...] = ()
    aux_detections: tuple[Detection, ...] = ()

    def __post_init__(self) -> None:
        if not (math.isfinite(self.pts) and math.isfinite(self.completed_at)):
            raise ValueError("LaneResult times must be finite")
        if not self.ok and self.requests:
            raise ValueError("a failed lane run (ok=False) must not carry requests")
