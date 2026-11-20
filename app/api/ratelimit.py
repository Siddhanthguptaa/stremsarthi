"""Small in-memory sliding-window limiter (per key). No persistence, no external state."""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowLimiter:
    """Allows ``limit`` events per ``window_s`` seconds for each key.

    ``check`` consumes a slot and returns ``(allowed, retry_after_s)``. Memory is bounded: each
    key keeps at most ``limit`` timestamps and idle keys are dropped as they age out.
    """

    def __init__(self, limit: int, window_s: float, max_keys: int = 256) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit must be >= 1 and window_s > 0")
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> tuple[bool, float]:
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, max(0.0, self.window_s - (now - hits[0]))
        hits.append(now)
        if len(self._hits) > self.max_keys:
            self._prune(now)
        return True, 0.0

    def _prune(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window_s]:
            del self._hits[key]
