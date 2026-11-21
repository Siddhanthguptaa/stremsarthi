"""Redaction before translation (docs/specs/audio.md 5.9): a caption never contains text of a muted span.

Every word that a ``MuteSpan`` (including fail-safe spans) covers, or that the spoken-PII rules flag, becomes
one mask token (``▮▮▮``; neighbouring masked words collapse into one) BEFORE the text goes to any API. In-process
only: words hold transcript text and must never be logged.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from contracts.speech import TranscriptWindow, Word

log = logging.getLogger("audio.captions")


@dataclass(frozen=True, slots=True)
class Piece:
    """A run of visible words (``text`` set) or a masked run (``text`` None)."""

    text: str | None
    t0: float
    t1: float


class MuteLedger:
    """Mute intervals seen so far (spans from the bus plus rule hits), pruned by age."""

    def __init__(self, keep_s: float = 30.0) -> None:
        self.keep_s = keep_s
        self._iv: deque[tuple[float, float]] = deque()

    def add(self, lo: float, hi: float) -> None:
        if hi > lo:
            self._iv.append((lo, hi))

    def prune(self, before_pts: float) -> None:
        keep = [(a, b) for a, b in self._iv if b >= before_pts - self.keep_s]
        self._iv = deque(keep)

    def intervals(self) -> list[tuple[float, float]]:
        return list(self._iv)

    def __len__(self) -> int:
        return len(self._iv)


def covered_s(intervals: Iterable[tuple[float, float]], lo: float, hi: float) -> float:
    """Seconds of ``[lo, hi]`` inside the union of ``intervals``."""
    pieces = sorted((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    total, cur = 0.0, lo
    for a, b in pieces:
        a = max(a, cur)
        if b > a:
            total += b - a
            cur = b
    return total


def word_flags(
    words: Sequence[Word], intervals: Sequence[tuple[float, float]], min_overlap_s: float = 0.06
) -> list[bool]:
    """True for a word that is muted: a mute covers at least ``min_overlap_s`` of it or at least half of it
    (a word touched only by the 0.2 s padding of a neighbour's span keeps its caption)."""
    out: list[bool] = []
    for w in words:
        dur = max(w.t1 - w.t0, 0.0)
        cov = covered_s(intervals, w.t0, w.t1)
        out.append(cov > 0.0 and (cov >= min_overlap_s or cov >= 0.5 * dur))
    return out


def mask_pieces(words: Sequence[Word], flags: Sequence[bool]) -> list[Piece]:
    pieces: list[Piece] = []
    for w, masked in zip(words, flags, strict=True):
        text = w.text.strip()
        if not masked and not text:
            continue
        if masked:
            if pieces and pieces[-1].text is None:
                pieces[-1] = Piece(None, pieces[-1].t0, w.t1)
            else:
                pieces.append(Piece(None, w.t0, w.t1))
        elif pieces and pieces[-1].text is not None:
            pieces[-1] = Piece(pieces[-1].text + " " + text, pieces[-1].t0, w.t1)
        else:
            pieces.append(Piece(text, w.t0, w.t1))
    return pieces


def pieces_text(pieces: Sequence[Piece], mask: str) -> str:
    return " ".join(mask if p.text is None else p.text for p in pieces)


def has_visible(pieces: Sequence[Piece]) -> bool:
    return any(p.text is not None for p in pieces)


# --------------------------------------------------------------------------- rule hits (defence in depth)
def rule_intervals(rules: object | None, words: Sequence[Word]) -> list[tuple[float, float]]:
    """Time ranges the spoken-PII rules flag in ``words`` (ambiguous ones included: an unclear case is masked in a
    caption). Independent of the mute verdict, so a caption is safe even before the span arrives. Never raises."""
    if rules is None or not words:
        return []
    try:
        win = TranscriptWindow(tuple(words), words[0].t0, words[-1].t1, 0)
        _, cands = rules.analyse(win)  # type: ignore[attr-defined]
        return [(float(c.t0), float(c.t1)) for c in cands]
    except Exception as exc:
        log.debug("caption rule check failed (%s)", type(exc).__name__)
        return []


# --------------------------------------------------------------------------- overlap removal
def norm_token(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in t if ch.isalnum() or unicodedata.category(ch).startswith("M"))


def drop_overlap(
    tail: Sequence[Word], new: Sequence[Word], prev_end: float | None, max_overlap: int = 4
) -> list[Word]:
    """Remove leading words of ``new`` that repeat the end of ``tail`` (the 0.4 s overlap of consecutive segments):
    the longest suffix/prefix match of up to ``max_overlap`` tokens that is also consistent in time."""
    cur = [w for w in new if norm_token(w.text)]
    tl = [w for w in tail if norm_token(w.text)][-max_overlap:]
    for k in range(min(max_overlap, len(cur), len(tl)), 0, -1):
        if [norm_token(w.text) for w in cur[:k]] != [norm_token(w.text) for w in tl[-k:]]:
            continue
        last = tl[-1]
        if any(w.approx for w in cur[:k]) or last.approx:
            ok = prev_end is not None and cur[k - 1].t0 < prev_end
        else:
            ok = cur[k - 1].t1 <= last.t1 + 0.05
        if ok:
            return cur[k:]
    return cur
