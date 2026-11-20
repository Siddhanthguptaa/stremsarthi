"""Back-pressure helpers: a concurrency tracker, stale-request drop and region clamping.

Adapted from PrivaStream ``src/web/backend/video_api_utils.py`` (Apache-2.0, Saximn/privastream;
licence text in ``engine/THIRD_PARTY_LICENSES/privastream/LICENSE``). Changes: only
``now_ms``, ``is_stale_request``, ``RequestTracker`` and ``clamp_region`` are kept (the image
decoding, embedding and Flask helpers are not used); ``RequestTracker.try_start`` and the
monotonic-clock ``is_stale`` were added.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def is_stale_request(
    request_timestamp_ms: Any,
    max_age_ms: int,
    *,
    enabled: bool = True,
    current_time_ms: int | None = None,
) -> bool:
    """Return True when a client timestamp is too old or malformed."""
    if not enabled:
        return False
    try:
        timestamp = int(request_timestamp_ms)
    except (TypeError, ValueError):
        return True
    current = current_time_ms if current_time_ms is not None else now_ms()
    return current - timestamp > max_age_ms


def is_stale(created_at: float, max_age_s: float, *, now: float | None = None) -> bool:
    """Monotonic-clock variant for in-process queues. A non-finite stamp counts as stale."""
    if not math.isfinite(created_at):
        return True
    current = time.monotonic() if now is None else now
    return current - created_at > max_age_s


class RequestTracker:
    """Thread-safe concurrent request counter."""

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max(1, int(max_concurrent))
        self.active = 0
        self._lock = threading.Lock()

    def can_start(self) -> bool:
        with self._lock:
            return self.active < self.max_concurrent

    def start(self) -> int:
        with self._lock:
            self.active += 1
            return self.active

    def try_start(self) -> bool:
        """Atomic check-and-start: False (and no change) when the tracker is full."""
        with self._lock:
            if self.active >= self.max_concurrent:
                return False
            self.active += 1
            return True

    def finish(self) -> int:
        with self._lock:
            self.active = max(0, self.active - 1)
            return self.active

    def configure(self, max_concurrent: int) -> None:
        with self._lock:
            self.max_concurrent = max(1, int(max_concurrent))

    @contextmanager
    def track(self) -> Iterator[None]:
        self.start()
        try:
            yield
        finally:
            self.finish()


def clamp_region(frame_shape: tuple[int, ...], region: Iterable[Any]) -> tuple[int, int, int, int] | None:
    """Normalize and clamp [x1, y1, x2, y2] to image bounds."""
    values = list(region)
    if len(values) < 4:
        return None
    try:
        x1, y1, x2, y2 = (int(float(v)) for v in values[:4])
    except (TypeError, ValueError, OverflowError):
        return None
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    h, w = frame_shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2
