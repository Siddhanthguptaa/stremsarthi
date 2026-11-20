"""Plugin interfaces. Modules talk to each other only through these and the data contracts."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

from .chat import ChatMessage, ChatVerdict
from .config import CategoryConfig
from .detection import Detection
from .enums import Category, Device, Lane, Topic
from .geometry import Quad
from .media import Frame
from .privacy import check_codes
from .speech import PiiSpan, Transcript, TranscriptWindow


# --------------------------------------------------------------------------- video
@runtime_checkable
class AllowlistView(Protocol):
    """Read-only view of the enrolled-face allowlist (L2-normalised SFace vectors, RAM only)."""

    version: int

    def embeddings(self) -> Sequence[tuple[str, Any]]:
        """(face_id, embedding vector) pairs."""
        ...


@dataclass(frozen=True, slots=True)
class OcrResult:
    """One OCR pass over one frame. ``texts`` is free text that lives for that frame only:
    never log it, store it, or copy it into ``Detection.extra``."""

    boxes: tuple[Quad, ...]
    texts: tuple[str, ...]
    confs: tuple[float, ...]
    scripts: tuple[
        str, ...
    ]  # per box: "latin", "devanagari", "tamil", "telugu", "kannada", "bengali", "unknown"

    def __post_init__(self) -> None:
        if not (len(self.boxes) == len(self.texts) == len(self.confs) == len(self.scripts)):
            raise ValueError("OcrResult fields must have equal lengths")
        if not all(0.0 <= c <= 1.0 and math.isfinite(c) for c in self.confs):
            raise ValueError("OcrResult.confs must be within 0..1")
        check_codes("OcrResult", "scripts", self.scripts)

    def __repr__(self) -> str:
        return f"OcrResult(n_boxes={len(self.boxes)})"


@runtime_checkable
class OcrProvider(Protocol):
    """Memoised per ``frame.seq`` so cards, plates, text and screens share one OCR pass."""

    def __call__(self, frame: Frame) -> OcrResult: ...


@runtime_checkable
class Resources(Protocol):
    clock: Callable[[], float]  # time.monotonic
    allowlist: AllowlistView
    ocr: OcrProvider

    def ort_session(self, model_id: str) -> Any:
        """An ``onnxruntime.InferenceSession`` honouring ``gpu.*`` config and the arena limit."""
        ...

    def model_path(self, model_id: str) -> Path: ...


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    categories: Mapping[Category, CategoryConfig]
    device: Device
    ort_provider: Literal["cpu", "cuda"] = "cpu"
    gpu_mem_limit_mb: int = 0
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DetectContext:
    lane: Lane
    categories: Mapping[Category, CategoryConfig]
    stride: int
    previous: tuple[Detection, ...] = ()  # this lane's previous result
    deadline: float | None = None  # monotonic hint: stop refining after this
    allowlist: AllowlistView | None = None


@runtime_checkable
class Detector(Protocol):
    """Video detector plugin.

    Discovery: ``vision/detectors/<id>/__init__.py`` exports ``DETECTOR: type[Detector]``.
    Threading: ``detect`` is called by one lane thread at a time. It must not retain
    ``frame.image`` after returning, and must never contact the network (frames stay local).
    """

    id: ClassVar[str]
    lane: ClassVar[Lane]
    categories: ClassVar[frozenset[Category]]
    device: ClassVar[Device]
    vram_mb: ClassVar[int]  # declared estimate; checked against the profile budget

    def configure(self, cfg: DetectorConfig, res: Resources) -> None: ...
    def warmup(self) -> None: ...
    def detect(self, frame: Frame, ctx: DetectContext) -> Sequence[Detection]: ...
    def close(self) -> None: ...


@runtime_checkable
class FaceEnroller(Protocol):
    """Loaded by the API through ``enrol.impl`` so ``app/api`` never imports ``vision``."""

    def enrol(self, images: Sequence[Any], label: str) -> tuple[str, tuple[float, ...]]:
        """Return (face_id, L2-normalised embedding). Raises ValueError if too few usable faces."""
        ...


# --------------------------------------------------------------------------- audio
@runtime_checkable
class SpeechToText(Protocol):
    name: ClassVar[str]

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Transcript:
        """Must honour its own timeout and raise ``TimeoutError``/provider errors; never block the loop."""
        ...


@runtime_checkable
class PiiSpanDetector(Protocol):
    async def detect(self, window: TranscriptWindow) -> Sequence[PiiSpan]: ...


# --------------------------------------------------------------------------- chat
@runtime_checkable
class ChatClassifier(Protocol):
    async def classify(self, batch: Sequence[ChatMessage]) -> Sequence[ChatVerdict]: ...


# --------------------------------------------------------------------------- event bus
@runtime_checkable
class EventBus(Protocol):
    def publish(self, topic: Topic, event: Any) -> None:
        """Non-blocking. A slow subscriber drops its oldest events, except ``Topic.HOLD``."""
        ...

    def subscribe(self, topics: Sequence[Topic]) -> AsyncIterator[tuple[Topic, Any]]: ...
