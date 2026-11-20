"""Sample-accurate audio mutes on the 48 kHz output timeline (docs/specs/engine.md 5.6, T3.4).

New code, no upstream. Every ``MuteSpan`` covers ``[round(pts_start * 48000), round(pts_end * 48000))``
in absolute-sample coordinates (the same index the audio ring uses). The gain falls from 1 to 0 over
the first 10 ms of a span and rises back over the last 10 ms (both inside the span, which the audio
module has already padded), so everything between the fades is exactly zero and every sample outside
the span is untouched. ``mode="bleep"`` replaces the speech with a 1 kHz sine at -18 dBFS under the
same envelope; the sine is a function of the absolute sample index, so it is phase-continuous across
frame slices.

Book rules:

* overlapping or adjacent spans merge into one range (one pair of fades at the outer edges); if the
  members disagree on the mode, ``silence`` wins;
* a span is *late* when every one of its samples has already been emitted (counted, not stored); a
  span whose start has been emitted but not its end is *partly late*: the rest of it is applied and
  the leaked head is reported. Spans that merely arrive after ``start_arrival + D - 0.1`` but before
  their samples leave are applied normally (the session counts them as tardy);
* a later span with the same ``span_id`` replaces the earlier one only while that one has not started
  to leave and is not a ``failsafe`` span; otherwise the two are united (coverage never shrinks);
* ``granularity="failsafe"`` spans are final: they are never replaced, shortened or removed once
  accepted (only pruned after their samples have all been emitted);
* retroactive-hold ranges (``add_silence``) are hard zeros without fades, kept in the book so audio
  that reaches the ring late for an already-held time range is silenced as well;
* the book is bounded without ever forgetting audio still to be muted: only spans whose samples have
  all been emitted are evicted; when the cap is reached with every span unemitted the oldest two are
  united (over-muting the gap, ``final``), and hard ranges beyond 64 unite the closest pair.

Cost: the envelope is computed for the emitted slice only (about 1600 samples per frame at 30 fps).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

from contracts import MuteSpan

RATE = 48_000
FADE_SAMPLES = RATE // 100  # 10 ms
BLEEP_HZ = 1000.0
MAX_HARD_RANGES = 64
BLEEP_AMPLITUDE = round(32767 * 10 ** (-18.0 / 20.0))


@dataclass(frozen=True, slots=True)
class MuteRange:
    span_id: str
    start: int
    end: int  # exclusive
    mode: str
    final: bool = False  # granularity == "failsafe"


@dataclass(frozen=True, slots=True)
class MuteOutcome:
    """What ``MuteBook.add`` did with a span. Truthy when any of the span can still be applied."""

    accepted: bool
    late: bool = False  # every sample of the span had already been emitted
    leaked_samples: int = 0  # samples at the head of the span that had already been emitted
    replaced: bool = False  # revised an earlier span with the same id

    def __bool__(self) -> bool:
        return self.accepted


def span_to_range(span: MuteSpan) -> MuteRange:
    return MuteRange(
        span.span_id,
        round(span.pts_start * RATE),
        round(span.pts_end * RATE),
        span.mode,
        span.granularity == "failsafe",
    )


def gain_slice(length: int, lo: int, hi: int) -> np.ndarray:
    """Gain of samples ``lo..hi`` of a ``length``-sample span: 1 -> 0 -> 1, exactly 0 in the middle."""
    if length < 2 * FADE_SAMPLES:
        return np.zeros(hi - lo, np.float32)  # too short for two fades: hard silence
    k = np.arange(lo, hi, dtype=np.float32)
    edge = np.minimum(k, (length - 1) - k) * (1.0 / FADE_SAMPLES)
    return 1.0 - np.minimum(edge, 1.0)


def gain_envelope(length: int) -> np.ndarray:
    """Per-sample gain over a whole span of ``length`` samples."""
    return gain_slice(length, 0, length)


def apply_range(pcm: np.ndarray, first_index: int, rng: MuteRange, mode: str | None = None) -> bool:
    """Mute the part of ``rng`` that lies inside ``pcm`` (``pcm[0]`` is sample ``first_index``), in place."""
    lo, hi = max(rng.start, first_index), min(rng.end, first_index + len(pcm))
    if hi <= lo:
        return False
    gain = gain_slice(rng.end - rng.start, lo - rng.start, hi - rng.start)
    part = pcm[lo - first_index : hi - first_index]
    if not gain.any() and (mode or rng.mode) != "bleep":
        part[:] = 0  # the common case: the whole slice lies between the fades
        return True
    out = part.astype(np.float32) * gain
    if (mode or rng.mode) == "bleep":
        idx = np.arange(lo, hi, dtype=np.float64)
        out += (1.0 - gain) * (BLEEP_AMPLITUDE * np.sin(2.0 * math.pi * BLEEP_HZ * idx / RATE)).astype(
            np.float32
        )
    part[:] = np.clip(np.rint(out), -32768, 32767).astype(np.int16)
    return True


def merge_ranges(ranges: list[MuteRange]) -> list[MuteRange]:
    """Union of overlapping or adjacent ranges, sorted. ``silence`` wins over ``bleep``; final is sticky."""
    merged: list[MuteRange] = []
    for rng in sorted(ranges, key=lambda r: (r.start, r.end)):
        if merged and rng.start <= merged[-1].end:
            last = merged[-1]
            mode = "silence" if "silence" in (last.mode, rng.mode) else last.mode
            merged[-1] = MuteRange(
                last.span_id, last.start, max(last.end, rng.end), mode, last.final or rng.final
            )
        else:
            merged.append(rng)
    return merged


class MuteBook:
    """Thread-safe set of spans, applied to each emitted audio slice.

    The book owns the emit cursor (``emitted_through``: samples below it have left the engine) so that
    "is this span late?" and "apply this span to the slice" are decided under one lock: a span can
    never be judged late for a slice that has not been cut yet.
    """

    def __init__(self, max_spans: int = 1024) -> None:
        self._lock = threading.Lock()
        self._spans: dict[str, MuteRange] = {}
        self._merged: list[MuteRange] = []
        self._hard: list[tuple[int, int]] = []
        self._max = max_spans
        self._serial = 0
        self.emitted_through = -1
        self.mode_override: str | None = None  # runtime ``audio.mute_mode``; None follows each span
        self.accepted = self.late = self.partly_late = self.replaced = self.united = 0

    # ------------------------------------------------------------------ adding

    def add(self, span: MuteSpan) -> MuteOutcome:
        return self.add_range(span_to_range(span))

    def add_range(self, rng: MuteRange) -> MuteOutcome:
        with self._lock:
            if rng.end <= rng.start:
                return MuteOutcome(False)
            if rng.end <= self.emitted_through:
                self.late += 1
                return MuteOutcome(False, late=True, leaked_samples=rng.end - rng.start)
            leaked = max(0, self.emitted_through - rng.start)
            if leaked:
                self.partly_late += 1
            old = self._spans.get(rng.span_id)
            replaced = False
            if old is not None:
                if old.final or old.start < self.emitted_through:
                    # never shrink: unite with what is already accepted (failsafe is final)
                    rng = MuteRange(
                        rng.span_id,
                        min(old.start, rng.start),
                        max(old.end, rng.end),
                        "silence" if "silence" in (old.mode, rng.mode) else rng.mode,
                        old.final or rng.final,
                    )
                replaced = True
                self.replaced += 1
            else:
                self.accepted += 1
            self._spans[rng.span_id] = rng
            if len(self._spans) > self._max:
                self._shrink_spans_locked()
            self._merged = merge_ranges(list(self._spans.values()))
            return MuteOutcome(True, leaked_samples=leaked, replaced=replaced)

    def add_silence(self, start: int, end: int) -> None:
        """Hard-zero ``[start, end)`` (retroactive hold): no fades, not a span, never reported as a mute."""
        if end <= start:
            return
        with self._lock:
            self._hard.append((start, end))
            if len(self._hard) > MAX_HARD_RANGES:
                self._shrink_hard_locked()

    def _shrink_spans_locked(self) -> None:
        """Bound the span table without ever forgetting audio that still has to be muted.

        Spans whose samples have all left are dropped first. If the table is still over its cap (every
        span is unemitted: only possible with a burst of more than ``max_spans`` spans inside the delay
        window), the two oldest spans are united into one that covers the gap between them: over-muting
        by a few hundred milliseconds is safe, dropping a span is not. A united span is ``final`` so a
        later revision of either id cannot shrink it. Nothing is ever evicted while unemitted."""
        through = self.emitted_through
        for key in [k for k, r in self._spans.items() if r.end <= through]:
            del self._spans[key]
        while len(self._spans) > self._max and len(self._spans) >= 2:
            first, second = sorted(self._spans.values(), key=lambda r: (r.start, r.end))[:2]
            mode = "silence" if "silence" in (first.mode, second.mode) else first.mode
            united = MuteRange(first.span_id, first.start, max(first.end, second.end), mode, True)
            del self._spans[second.span_id]
            self._spans[first.span_id] = united
            self.united += 1

    def _shrink_hard_locked(self) -> None:
        """Keep at most ``MAX_HARD_RANGES`` hard-silence ranges by uniting the closest pair (never by
        dropping one): retroactive-hold silence must cover every sample it covered before."""
        self._hard.sort()
        while len(self._hard) > MAX_HARD_RANGES:
            best = min(
                range(len(self._hard) - 1),
                key=lambda i: self._hard[i + 1][0] - self._hard[i][1],
            )
            a, b = self._hard[best], self._hard[best + 1]
            self._hard[best : best + 2] = [(min(a[0], b[0]), max(a[1], b[1]))]

    # ------------------------------------------------------------------ applying

    def apply(self, pcm: np.ndarray, first_index: int, *, advance: bool = True) -> int:
        """Mute ``pcm`` in place and mark it emitted; returns how many ranges touched it."""
        end = first_index + len(pcm)
        with self._lock:
            hits = [r for r in self._merged if r.end > first_index and r.start < end]
            hard = [h for h in self._hard if h[1] > first_index and h[0] < end]
            mode = self.mode_override
            n = sum(apply_range(pcm, first_index, r, mode) for r in hits)
            for lo, hi in hard:
                pcm[max(lo, first_index) - first_index : min(hi, end) - first_index] = 0
            if advance:
                self._advance_locked(end)
        return n + len(hard)

    def advance(self, through: int) -> None:
        """Samples below ``through`` have left the engine (silent slices, which skip ``apply``)."""
        with self._lock:
            self._advance_locked(through)

    def _advance_locked(self, through: int) -> None:
        self.emitted_through = through
        if self._merged and self._merged[0].end <= through:
            self._spans = {k: r for k, r in self._spans.items() if r.end > through}
            self._merged = merge_ranges(list(self._spans.values()))
        if self._hard and any(h[1] <= through for h in self._hard):
            self._hard = [h for h in self._hard if h[1] > through]

    def prune(self, emitted_through: int | None = None) -> None:
        with self._lock:
            self._advance_locked(self.emitted_through if emitted_through is None else emitted_through)

    # ------------------------------------------------------------------ lifecycle

    def clear(self) -> None:
        """Forget every span (a new PTS clock: old sample numbers mean nothing)."""
        with self._lock:
            self._spans.clear()
            self._merged = []
            self._hard.clear()

    def reset_cursor(self) -> None:
        with self._lock:
            self.emitted_through = -1

    def __len__(self) -> int:
        return len(self._spans)

    @property
    def merged_ranges(self) -> list[MuteRange]:
        with self._lock:
            return list(self._merged)
