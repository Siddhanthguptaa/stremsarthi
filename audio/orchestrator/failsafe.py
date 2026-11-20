"""Deadline arithmetic, the PTS <-> arrival map and the already-muted coverage set (docs/specs/audio.md 5.3).

* soft deadline of a segment: ``arrival + D - 0.3`` (a voiced segment with no verdict by then is muted whole);
* hard acceptance by the engine: ``arrival + D - 0.1``. A span planned after that is clipped to the part of
  the audio that has not been released yet (``ArrivalMap.pts_at``).
"""

from __future__ import annotations

from collections import deque

SOFT_MARGIN_S = 0.3
HARD_MARGIN_S = 0.1


def soft_deadline(arrival: float, delay_s: float) -> float:
    return arrival + delay_s - SOFT_MARGIN_S


def hard_deadline(arrival: float, delay_s: float) -> float:
    return arrival + delay_s - HARD_MARGIN_S


class ArrivalMap:
    """Maps source PTS to the monotonic time its sample arrived (slope 1 per segment record, bounded)."""

    def __init__(self, maxlen: int = 128) -> None:
        self._rec: deque[tuple[float, float]] = deque(maxlen=maxlen)  # (pts_start, arrival)

    def __len__(self) -> int:
        return len(self._rec)

    def add(self, pts_start: float, arrival: float) -> None:
        self._rec.append((pts_start, arrival))

    def arrival_of(self, pts: float) -> float | None:
        if not self._rec:
            return None
        best = self._rec[0]
        for rec in self._rec:
            if rec[0] <= pts:
                best = rec
        return best[1] + (pts - best[0])

    def pts_at(self, arrival: float) -> float | None:
        if not self._rec:
            return None
        best = self._rec[0]
        for rec in self._rec:
            if rec[1] <= arrival:
                best = rec
        return best[0] + (arrival - best[1])


class Coverage:
    """Sorted, merged intervals that were already emitted as mutes (bounded by ``prune``)."""

    def __init__(self) -> None:
        self._iv: list[tuple[float, float]] = []

    def __len__(self) -> int:
        return len(self._iv)

    def add(self, lo: float, hi: float) -> None:
        if hi < lo:
            return
        out: list[tuple[float, float]] = []
        placed = False
        for a, b in self._iv:
            if b < lo - 1e-9:
                out.append((a, b))
            elif a > hi + 1e-9:
                if not placed:
                    out.append((lo, hi))
                    placed = True
                out.append((a, b))
            else:
                lo, hi = min(lo, a), max(hi, b)
        if not placed:
            out.append((lo, hi))
        self._iv = out

    def uncovered(self, lo: float, hi: float, min_len: float = 0.02) -> list[tuple[float, float]]:
        pieces: list[tuple[float, float]] = []
        cur = lo
        for a, b in self._iv:
            if b <= cur:
                continue
            if a >= hi:
                break
            if a > cur:
                pieces.append((cur, min(a, hi)))
            cur = max(cur, b)
            if cur >= hi:
                break
        if cur < hi:
            pieces.append((cur, hi))
        return [(a, b) for a, b in pieces if b - a >= min_len]

    def prune(self, before: float) -> None:
        self._iv = [(a, b) for a, b in self._iv if b >= before]
