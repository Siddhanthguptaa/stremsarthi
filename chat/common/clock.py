"""Injectable time. ``ManualClock`` makes backoff, polling and quota tests instant and deterministic."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def wall(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class ManualClock:
    """Virtual time: ``sleep`` advances the clock immediately and yields to the event loop once.

    ``sleeps`` records every requested sleep so tests can assert on intervals.
    """

    def __init__(self, wall: float = 1_700_000_000.0, mono: float = 1000.0) -> None:
        self._wall = wall
        self._mono = mono
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self._mono

    def wall(self) -> float:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._mono += seconds
        self._wall += seconds

    async def sleep(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        self.sleeps.append(seconds)
        self.advance(seconds)
        await asyncio.sleep(0)
