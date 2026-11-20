"""Event contracts: safety log, risk meter, hold, captions, status, allowlist, timings."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .enums import Category, HoldSource, Modality
from .geometry import Quad
from .privacy import CODE_RE, check_codes, validate_instance

SafetyAction = Literal["masked", "muted", "hidden", "held", "deleted", "timed_out", "warned", "failsafe"]
RiskSource = Literal["music", "fingerprint", "profanity", "risky_text", "slur"]
RiskLevel = Literal["green", "yellow", "orange", "red"]
_SAFETY_ACTIONS = ("masked", "muted", "hidden", "held", "deleted", "timed_out", "warned", "failsafe")
_RISK_SOURCES = ("music", "fingerprint", "profanity", "risky_text", "slur")
_RISK_LEVELS = ("green", "yellow", "orange", "red")
_JSON_PRIMITIVES = (str, int, float, bool, type(None))


@dataclass(frozen=True, slots=True)
class SafetyEvent:
    """The only thing the safety report is built from. Never holds text, images or raw PII."""

    event_id: str
    session_id: str
    wall_ts: str  # UTC ISO-8601
    pts: float | None
    modality: Modality
    category: Category
    action: SafetyAction
    confidence: float
    bbox: Quad | None  # pixels, video only
    duration_ms: int | None
    source: str
    detail_code: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("SafetyEvent.confidence must be within 0..1")
        if self.action not in _SAFETY_ACTIONS:
            raise ValueError("invalid SafetyEvent.action")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class RiskSignal:
    wall_ts: str
    pts: float | None
    source: RiskSource
    category: Category
    score: float  # 0..1
    ttl_s: float  # the signal decays to zero after this
    label: str  # code, e.g. "fp:demo_03"

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("RiskSignal.score must be within 0..1")
        if not (self.ttl_s > 0 and math.isfinite(self.ttl_s)):
            raise ValueError("RiskSignal.ttl_s must be > 0")
        if self.source not in _RISK_SOURCES:
            raise ValueError("invalid RiskSignal.source")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class RiskMeter:
    """Aggregated strike-guard level shown to the streamer (not a RiskSignal)."""

    wall_ts: str
    level: RiskLevel
    score: float
    top_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("RiskMeter.score must be within 0..1")
        if self.level not in _RISK_LEVELS:
            raise ValueError("invalid RiskMeter.level")
        check_codes("RiskMeter", "top_sources", self.top_sources)
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class HoldCommand:
    source: HoldSource
    active: bool
    actor: str  # opaque hash, never a display name

    def __post_init__(self) -> None:
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class SessionCommand:
    """Go-live lifecycle, published by the API on ``control.session``."""

    action: Literal["start", "stop"]
    actor: str

    def __post_init__(self) -> None:
        if self.action not in ("start", "stop"):
            raise ValueError("invalid SessionCommand.action")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class CaptionEvent:
    pts_start: float
    pts_end: float
    lang: str  # e.g. "hi", "kn", "ta", "te", "bn", "en"
    text: str  # free text: shown on the overlay, never logged or stored
    is_final: bool

    def __post_init__(self) -> None:
        if self.pts_end < self.pts_start:
            raise ValueError("CaptionEvent.pts_end must be >= pts_start")
        validate_instance(self)

    def __repr__(self) -> str:
        return f"CaptionEvent(lang={self.lang!r}, pts_start={self.pts_start}, final={self.is_final})"


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage: str  # e.g. "ingest", "lane_fast", "stt", "compose", "encode"
    seq: int
    pts: float | None
    t_start: float  # time.monotonic()
    t_end: float

    def __post_init__(self) -> None:
        if self.t_end < self.t_start:
            raise ValueError("StageTiming.t_end must be >= t_start")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """A runtime toggle published by the API, as a dotted-key diff,
    e.g. ``{"categories.face.enabled": False}``. Values are JSON primitives or lists of them."""

    diff: Mapping[str, Any]
    actor: str

    def __post_init__(self) -> None:
        if not self.diff:
            raise ValueError("ConfigChange.diff must not be empty")
        for key, value in self.diff.items():
            if not CODE_RE.match(key):
                raise ValueError("ConfigChange keys must be dotted config paths")
            items = value if isinstance(value, (list, tuple)) else (value,)
            if not all(isinstance(v, _JSON_PRIMITIVES) for v in items):
                raise ValueError("ConfigChange values must be JSON primitives or lists of them")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class AllowlistUpdate:
    """Face allowlist change, API to engine over local IPC only. The embedding is
    biometric-derived: it is RAM-only and redacted from ``repr``.

    ``snapshot`` means "replace the allowlist: ``add`` updates up to ``version`` follow"."""

    op: Literal["add", "remove", "snapshot"]
    face_id: str | None
    label: str | None
    embedding: tuple[float, ...] | None
    version: int

    def __post_init__(self) -> None:
        if self.op not in ("add", "remove", "snapshot"):
            raise ValueError("invalid AllowlistUpdate.op")
        if self.op == "add" and not (self.face_id and self.embedding):
            raise ValueError("add needs face_id and a non-empty embedding")
        if self.op == "remove" and not self.face_id:
            raise ValueError("remove needs face_id")
        if self.op == "snapshot" and (self.face_id is not None or self.embedding is not None):
            raise ValueError("snapshot carries no face_id or embedding")
        if self.embedding is not None and not all(math.isfinite(x) for x in self.embedding):
            raise ValueError("embedding must be finite")
        validate_instance(self)

    def __repr__(self) -> str:
        n = 0 if self.embedding is None else len(self.embedding)
        return f"AllowlistUpdate(op={self.op!r}, face_id={self.face_id!r}, embedding=<{n} floats>, version={self.version})"


@dataclass(frozen=True, slots=True)
class EnrolledFaceMeta:
    face_id: str
    label: str  # free text (a name): never logged
    created_at: str
    expires_at: str | None

    def __post_init__(self) -> None:
        validate_instance(self)

    def __repr__(self) -> str:
        return f"EnrolledFaceMeta(face_id={self.face_id!r})"


@dataclass(frozen=True, slots=True)
class EngineStatus:
    state: Literal["starting", "ready", "waiting_ingest", "live", "stopping", "error"]
    hold_active: bool
    hold_sources: tuple[HoldSource, ...]
    delay_s_p50: float | None
    fps: float | None
    cpu_cores: float | None
    vram_mb: int | None
    allowlist_version: int
    lanes_stale: tuple[str, ...]
    protect_seconds: float
    contract_version: str

    def __post_init__(self) -> None:
        if self.state not in ("starting", "ready", "waiting_ingest", "live", "stopping", "error"):
            raise ValueError("invalid EngineStatus.state")
        check_codes("EngineStatus", "lanes_stale", self.lanes_stale)
        validate_instance(self)
