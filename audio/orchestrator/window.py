"""Rolling transcript window on absolute PTS (docs/specs/audio.md 5.4). In-process only: never log its text."""

from __future__ import annotations

import unicodedata
from collections import deque
from collections.abc import Sequence

from contracts.speech import TranscriptWindow, Word


def norm(text: str) -> str:
    """Lowercase NFKC text without punctuation (combining marks of Indic scripts are kept)."""
    t = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in t if ch.isalnum() or unicodedata.category(ch).startswith("M"))


class TranscriptWindowBuilder:
    def __init__(self, span_s: float = 6.0, max_overlap_tokens: int = 4) -> None:
        self.span_s = span_s
        self.max_overlap = max_overlap_tokens
        self._words: deque[Word] = deque()

    def __len__(self) -> int:
        return len(self._words)

    def add(
        self, words: Sequence[Word], pts_start: float, pts_end: float, prev_end: float | None = None
    ) -> TranscriptWindow:
        """Add a segment's words. Leading words that repeat the previous segment's tail (the 0.4 s overlap)
        are dropped: longest suffix/prefix match of up to 4 tokens, with the time condition
        ``t1 <= last.t1 + 0.05`` (or, for interpolated words, ``t0 < previous segment end``)."""
        floor = pts_end - self.span_s
        while self._words and self._words[0].t1 < floor:
            self._words.popleft()
        new = [w for w in words if norm(w.text)]
        tail = list(self._words)[-self.max_overlap :]
        for k in range(min(self.max_overlap, len(new), len(tail)), 0, -1):
            if [norm(w.text) for w in new[:k]] != [norm(w.text) for w in tail[-k:]]:
                continue
            last = tail[-1]
            if any(w.approx for w in new[:k]) or last.approx:
                ok = prev_end is not None and new[k - 1].t0 < prev_end
            else:
                ok = new[k - 1].t1 <= last.t1 + 0.05
            if ok:
                new = new[k:]
                break
        self._words.extend(new)
        new_from = len(self._words) - len(new)
        start = self._words[0].t0 if self._words else pts_start
        return TranscriptWindow(tuple(self._words), min(start, pts_start), pts_end, new_from)
