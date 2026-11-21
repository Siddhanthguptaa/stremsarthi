"""Caption layout and timing (docs/specs/audio.md 5.9).

Lines of at most ``max_chars`` characters, at most two lines per caption, shown at least 1.2 s and at most 4 s;
``pts_start`` is the first word's start, ``pts_end`` the last word's end plus 0.3 s. Text longer than two lines is
split over several captions that share the speech time in proportion to their length. Also the mapping from
source PTS to this process's clock, so the caption deadline ``pts_start + D - 0.3`` can be checked.
"""

from __future__ import annotations

import unicodedata
from collections import deque
from dataclasses import dataclass

from audio.captions.settings import CaptionSettings


def visible_len(text: str) -> int:
    """Characters a reader sees: combining marks of Indic scripts and zero-width characters do not count."""
    n = 0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Cf"):
            continue
        n += 1
    return n


def _hard_split(word: str, width: int) -> list[str]:
    out: list[str] = []
    cur = ""
    for ch in word:
        if visible_len(cur + ch) > width and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def wrap_lines(text: str, width: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        for part in _hard_split(word, width):
            if not cur:
                cur = part
            elif visible_len(cur) + 1 + visible_len(part) <= width:
                cur = f"{cur} {part}"
            else:
                lines.append(cur)
                cur = part
    if cur:
        lines.append(cur)
    return lines


def wrap_pages(text: str, width: int, max_lines: int) -> list[str]:
    """Captions of at most ``max_lines`` lines each, lines joined by a newline."""
    lines = wrap_lines(text, width)
    return ["\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]


@dataclass(frozen=True, slots=True)
class Slot:
    text: str
    pts_start: float
    pts_end: float


def schedule(pages: list[str], t0: float, t1: float, s: CaptionSettings) -> list[Slot]:
    """Time slots for ``pages`` of speech that ran from ``t0`` to ``t1``."""
    if not pages:
        return []
    last_end = t1 + s.tail_s
    if len(pages) == 1:
        end = min(max(last_end, t0 + s.min_show_s), t0 + s.max_show_s)
        return [Slot(pages[0], t0, max(end, t0))]
    weights = [max(1, visible_len(p)) for p in pages]
    total = float(sum(weights))
    span = max(t1 - t0, 0.0)
    slots: list[Slot] = []
    cum = 0.0
    for i, (p, w) in enumerate(zip(pages, weights, strict=True)):
        start = t0 + span * cum / total
        cum += w
        if i < len(pages) - 1:
            end = min(t0 + span * cum / total, start + s.max_show_s)
        else:
            end = min(max(last_end, start + s.min_show_s), start + s.max_show_s)
        slots.append(Slot(p, start, max(end, start)))
    return slots


class ClockMap:
    """Estimate of this process's clock at the capture time of a source PTS.

    A transcript of audio up to ``pts_end`` arrived at ``t_recv`` after ``latency_s`` of speech-to-text, so that
    audio was captured at about ``t_recv - latency_s``. The smallest recent offset is used (the earliest capture
    time), which makes the deadline estimate conservative."""

    def __init__(self, keep: int = 8) -> None:
        self._offsets: deque[float] = deque(maxlen=keep)

    def observe(self, pts_end: float, t_recv: float, latency_s: float) -> None:
        self._offsets.append((t_recv - max(latency_s, 0.0)) - pts_end)

    def clock_of(self, pts: float) -> float | None:
        if not self._offsets:
            return None
        return pts + min(self._offsets)

    def due(self, pts_start: float, delay_s: float, margin_s: float) -> float | None:
        c = self.clock_of(pts_start)
        return None if c is None else c + delay_s - margin_s
