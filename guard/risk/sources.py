"""Signal sources for the aggregator (spec 5.4). Guard never sees OCR text or audio here."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from contracts.detection import Detection
from contracts.enums import Category, Topic
from contracts.events import RiskSignal
from contracts.speech import Transcript

from .profanity import ProfanityDetector

RISKY_TEXT_TTL_S = 10.0


def _utc_iso(_pts: float | None) -> str:
    return datetime.now(UTC).isoformat()


def risky_text_signal(
    det: Detection, wall_ts_fn: Callable[[float | None], str] = _utc_iso
) -> RiskSignal | None:
    """``Detection(RISKY_TEXT, conf)`` becomes ``RiskSignal(risky_text, 0.5 * conf, ttl 10 s)``."""
    if det.category is not Category.RISKY_TEXT:
        return None
    return RiskSignal(
        wall_ts=wall_ts_fn(det.pts),
        pts=det.pts,
        source="risky_text",
        category=Category.RISKY_TEXT,
        score=0.5 * det.confidence,
        ttl_s=RISKY_TEXT_TTL_S,
        label="risky_text",
    )


class SignalSources:
    """Maps bus events to ``RiskSignal``s. ``RiskSignal``s from other guard parts pass through."""

    def __init__(
        self,
        profanity: ProfanityDetector | None = None,
        wall_ts_fn: Callable[[float | None], str] = _utc_iso,
    ) -> None:
        self.profanity = profanity or ProfanityDetector(wall_ts_fn=wall_ts_fn)
        self._wall = wall_ts_fn

    def from_event(self, topic: Topic, event: object) -> list[RiskSignal]:
        if topic is Topic.TRANSCRIPT and isinstance(event, Transcript):
            return self.profanity.feed_transcript(event)
        if topic is Topic.DETECTION and isinstance(event, Detection):
            sig = risky_text_signal(event, self._wall)
            return [sig] if sig else []
        if topic is Topic.RISK and isinstance(event, RiskSignal):
            return [event]
        return []
