"""Reference stand-ins for the Gate 5 legs (``--reference``): components that are right by construction (they answer
from the ground-truth labels, or are re-derived from the spec) so the harness, the criteria and the report can be
validated without the product code, the way ``eval/refengine.py`` does for gate 1 and ``ReferenceClassifier`` for gate 4.
Seeded defects (``--defect NAME``) each break exactly one criterion; the gate must exit 1 for every one of them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFECTS = ("music_blind", "fp_blind", "slow_meter", "no_deva_profanity", "late_captions", "basic_shaping")
LEVELS = ("green", "yellow", "orange", "red")
WEIGHTS = {"fingerprint": 1.0, "slur": 0.9, "risky_text": 0.6, "profanity": 0.5, "music": 0.35}


# ---------------------------------------------------------------------------------------------- music and fingerprint
def ref_music(clip: dict[str, Any], defect: str) -> tuple[bool, float | None]:
    """(detected, seconds from music start to ON). A good detector needs about 1.9 s (3 windows of 0.48 s hop)."""
    if defect == "music_blind" or not clip["music"]:
        return False, None
    return True, 1.9


def ref_fingerprint(snip: dict[str, Any], defect: str) -> str | None:
    if defect == "fp_blind" or snip["kind"] != "fp_positive":
        return None
    return str(snip["track"])


# ---------------------------------------------------------------------------------------------- profanity
def ref_profanity(utt: dict[str, Any], defect: str) -> list[float]:
    """Signal times (stream seconds) for one utterance: at the first hit word."""
    if not utt["profane"] or not utt["hit_words"]:
        return []
    if defect == "no_deva_profanity" and utt["group"] == "hinglish_deva":
        return []
    return [utt["t_offset"] + utt["words"][utt["hit_words"][0]][1]]


# ---------------------------------------------------------------------------------------------- risk meter
def _level(score: float, prev: int) -> int:
    lows = (0.0, 0.25, 0.50, 0.75)
    raw = max(i for i, lo in enumerate(lows) if score >= lo)
    if raw >= prev:
        return raw
    return prev if score >= lows[prev] - 0.05 else raw


@dataclass
class _Sig:
    t: float
    source: str
    score: float
    ttl: float


class RefRisk:
    """Spec 5.5 in the simplest form: half-life ttl/3, weights, noisy-OR, 0.05 hysteresis. Same ``handle``/``tick``
    surface the harness uses on the real ``RiskService``, but it answers with ``(level, score)`` tuples."""

    def __init__(self, defect: str = "") -> None:
        self.sigs: list[_Sig] = []
        self.idx = 0
        self.defect = defect
        self.last_pub = -1e9

    def add(self, source: str, score: float, ttl: float, now: float) -> None:
        self.sigs.append(_Sig(now, source, score, ttl))

    def score(self, now: float) -> float:
        self.sigs = [s for s in self.sigs if now - s.t <= s.ttl]
        p = 1.0
        for s in self.sigs:
            p *= 1.0 - WEIGHTS[s.source] * s.score * 2.0 ** (-(now - s.t) / (s.ttl / 3.0))
        return 1.0 - p

    def meter(self, now: float) -> tuple[str, float]:
        sc = self.score(now)
        if self.defect == "slow_meter" and self.sigs and now - min(s.t for s in self.sigs) < 2.6:
            return LEVELS[self.idx], sc  # the defect: the level lags 2.6 s behind the signal
        self.idx = _level(sc, self.idx)
        return LEVELS[self.idx], sc


# ---------------------------------------------------------------------------------------------- captions
def ref_caption_publish_time(seg: dict[str, Any], defect: str, delay_s: float = 4.0) -> float | None:
    """Stream-axis time a good caption service publishes a segment's caption (transcript arrival = segment end + STT
    latency, then the 0.25 s settle wait and the translation latency), or None when it would miss the display time
    minus the 0.3 s soft margin and skips the caption (``on_late = skip``). The ``late_captions`` defect ignores
    deadlines and publishes 2.5 s late."""
    t = seg["pts_end"] + seg["stt_latency_s"] + 0.25 + seg["translate_latency_s"]
    if defect == "late_captions":
        return t + 2.5
    return None if t > seg["pts_start"] + delay_s - 0.3 else t
