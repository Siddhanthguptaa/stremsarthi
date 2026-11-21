# Adapted from PrivaStream (Apache-2.0): src/models/audio/training/src/pii_detector.py
#   (_passes_luhn, and the "overlapping detections: keep the more confident one" merge).
# Changes: character spans became time/token spans; overlapping candidates are unioned instead of replaced.
# The upstream licence text is in audio/pii/LICENSE-privastream-Apache-2.0.
"""Candidate spans of the spoken-PII rules and their merge (docs/specs/audio.md 5.6)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from contracts.enums import Category


@dataclass(slots=True)
class Cand:
    """One rule hit. Token indices refer to the token list the rules built from the window."""

    category: Category
    t0: float
    t1: float
    confidence: float
    reason: str  # machine code, never text
    approx: bool
    widx0: int  # window word index range
    widx1: int
    tok0: int
    tok1: int
    n_digits: int = 0
    cue_active: bool = False
    ambiguous: bool = False
    provisional: bool = (
        False  # a fail-safe guess from the shape of the speech (digit run, spelt id), not a cue
    )

    def overlaps(self, other: Cand) -> bool:
        return self.tok0 <= other.tok1 and other.tok0 <= self.tok1


def is_ambiguous(confidence: float) -> bool:
    return 0.4 <= confidence < 0.7


def passes_luhn(digits: str) -> bool:
    """Luhn checksum of a 13-19 digit string (card numbers); repeated-digit strings fail."""
    if not digits.isdigit() or not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for idx, ch in enumerate(reversed(digits)):
        v = int(ch)
        if idx % 2 == 1:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return total % 10 == 0


def merge_candidates(cands: Iterable[Cand]) -> list[Cand]:
    """Union overlapping candidates. Category, reason and confidence come from the most confident member;
    the result is ambiguous only if every member was (a confident rule settles the question)."""
    out: list[Cand] = []
    for c in sorted(cands, key=lambda x: (x.t0, x.t1)):
        if out and out[-1].overlaps(c):
            m = out[-1]
            top = c if c.confidence > m.confidence else m
            out[-1] = Cand(
                top.category,
                min(m.t0, c.t0),
                max(m.t1, c.t1),
                top.confidence,
                top.reason,
                m.approx or c.approx,
                min(m.widx0, c.widx0),
                max(m.widx1, c.widx1),
                min(m.tok0, c.tok0),
                max(m.tok1, c.tok1),
                max(m.n_digits, c.n_digits),
                m.cue_active or c.cue_active,
                m.ambiguous and c.ambiguous,
                m.provisional and c.provisional,
            )
        else:
            out.append(c)
    return out
