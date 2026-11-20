"""Token bucket rate limiter, usable synchronously (``try_acquire``) and asynchronously (``acquire``)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    """``rate`` tokens per second, holding at most ``capacity``. Starts full.

    The clock and sleep are injectable (``ManualClock.monotonic`` / ``ManualClock.sleep``).
    """

    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(capacity)
        self._stamp = clock()
        self._lock: asyncio.Lock | None = None

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._stamp
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._stamp = now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def try_acquire(self, n: float = 1.0) -> bool:
        if n <= 0 or n > self.capacity:
            raise ValueError("n must be within (0, capacity]")
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def wait_time(self, n: float = 1.0) -> float:
        """Seconds until ``n`` tokens are available (0 when they already are)."""
        if n <= 0 or n > self.capacity:
            raise ValueError("n must be within (0, capacity]")
        self._refill()
        return max(0.0, (n - self._tokens) / self.rate)

    async def acquire(self, n: float = 1.0) -> None:
        """Wait (FIFO among concurrent callers) until ``n`` tokens are available, then take them."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while not self.try_acquire(n):
                await self._sleep(self.wait_time(n))
