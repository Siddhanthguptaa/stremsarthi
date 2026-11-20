"""Speech types. In-process only: transcript text never goes on the wire or into logs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .enums import Category
from .privacy import validate_instance


@dataclass(frozen=True, slots=True)
class Word:
    text: str  # free text, transient
    t0: float  # absolute source PTS
    t1: float
    approx: bool = False  # True: times interpolated over the segment (provider gave no word times)
    seg_id: int = 0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.t0) and math.isfinite(self.t1)) or self.t1 < self.t0:
            raise ValueError("Word needs finite times with t1 >= t0")

    def __repr__(self) -> str:
        return f"Word(t0={self.t0:.3f}, t1={self.t1:.3f}, approx={self.approx}, seg_id={self.seg_id})"


@dataclass(frozen=True, slots=True)
class Transcript:
    pts_start: float
    pts_end: float
    text: str  # free text: never logged
    words: tuple[Word, ...]
    lang: str
    provider: Literal["sarvam", "groq", "local"]
    latency_s: float

    def __post_init__(self) -> None:
        if self.pts_end < self.pts_start:
            raise ValueError("Transcript.pts_end must be >= pts_start")
        if self.provider not in ("sarvam", "groq", "local"):
            raise ValueError("invalid Transcript.provider")
        validate_instance(self)

    @property
    def has_word_times(self) -> bool:
        return bool(self.words) and not any(w.approx for w in self.words)

    def __repr__(self) -> str:
        return (
            f"Transcript(provider={self.provider!r}, lang={self.lang!r}, "
            f"pts=({self.pts_start:.3f}, {self.pts_end:.3f}), n_words={len(self.words)})"
        )


@dataclass(frozen=True, slots=True)
class TranscriptWindow:
    """Rolling transcript (last ~8 s, absolute PTS) so rules can see numbers split across segments."""

    words: tuple[Word, ...]
    pts_start: float
    pts_end: float
    new_from_index: int  # words[new_from_index:] arrived with the latest segment


@dataclass(frozen=True, slots=True)
class PiiSpan:
    category: Category
    pts_start: float
    pts_end: float
    confidence: float
    reason: str  # code
    granularity: Literal["word", "segment"]
    ambiguous: bool = False  # True: needs the LLM; if it times out and speech is voiced, mute

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("PiiSpan.confidence must be within 0..1")
        if self.pts_end < self.pts_start:
            raise ValueError("PiiSpan.pts_end must be >= pts_start")
        if self.granularity not in ("word", "segment"):
            raise ValueError("invalid PiiSpan.granularity")
        validate_instance(self)
