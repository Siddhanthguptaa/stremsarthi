"""Exponential backoff with bounded jitter."""

from __future__ import annotations

import random
from collections.abc import Callable


class Backoff:
    def __init__(
        self,
        base: float = 1.0,
        cap: float = 60.0,
        factor: float = 2.0,
        jitter: float = 0.2,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if base <= 0 or cap < base or factor < 1 or not 0 <= jitter < 1:
            raise ValueError("invalid backoff parameters")
        self.base, self.cap, self.factor, self.jitter, self._rng = base, cap, factor, jitter, rng
        self._attempt = 0

    @property
    def attempt(self) -> int:
        return self._attempt

    def next(self) -> float:
        raw = min(self.cap, self.base * self.factor**self._attempt)
        self._attempt += 1
        # symmetric jitter, then clamp to the cap
        spread = raw * self.jitter * (2 * self._rng() - 1)
        return min(self.cap, max(0.0, raw + spread))

    def reset(self) -> None:
        self._attempt = 0
