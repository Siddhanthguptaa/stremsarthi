"""Ground-truth records for the audio suite and their JSON form (no audio, no free text beyond the SPECIMEN words)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PII_KINDS = ("phone", "otp", "upi", "address", "aadhaar")


@dataclass
class WordTruth:
    text: str  # what a recogniser would write (native script or Latin)
    tts: str  # what the TTS was asked to say
    t0: float
    t1: float
    kind: str | None = None  # PII kind or None
    digit: bool = (
        False  # a spoken digit of a number (phone, otp, aadhaar, digits inside an upi id or pincode)
    )
    span: int | None = None  # index into ClipTruth.spans
    hard_neg: bool = False  # a number-bearing non-PII phrase (score, level, price...): must NOT be muted

    @property
    def pii(self) -> bool:
        return self.kind is not None


@dataclass
class SpanTruth:
    kind: str
    t0: float
    t1: float
    first_word: int
    last_word: int  # inclusive
    cue: str  # "lexicon" (a common cue phrase) | "paraphrase" | "none"
    n_digits: int = 0
    value: str = ""  # the SPECIMEN value that was spoken (digits / id / address), for the lint only


@dataclass
class ClipTruth:
    clip_id: str
    lang: str
    voice: str
    speed: float
    snr_db: float | None
    noise: str  # none | white | pink | babble | music
    bucket: str  # core | hard | control
    file: str
    duration_s: float
    pcm_sha256: str
    words: list[WordTruth] = field(default_factory=list)
    spans: list[SpanTruth] = field(default_factory=list)
    face_ranges: list[tuple[float, float]] = field(
        default_factory=list
    )  # allowlisted face visible (video side)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["face_ranges"] = [list(r) for r in self.face_ranges]
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ClipTruth:
        return ClipTruth(
            clip_id=d["clip_id"],
            lang=d["lang"],
            voice=d["voice"],
            speed=d["speed"],
            snr_db=d["snr_db"],
            noise=d["noise"],
            bucket=d["bucket"],
            file=d["file"],
            duration_s=d["duration_s"],
            pcm_sha256=d["pcm_sha256"],
            words=[WordTruth(**w) for w in d["words"]],
            spans=[SpanTruth(**s) for s in d["spans"]],
            face_ranges=[(a, b) for a, b in d.get("face_ranges", [])],
        )
