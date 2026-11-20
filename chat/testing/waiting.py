"""Polling helper for async tests (the fakes run on real sockets, so state changes need real loop turns)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable


async def until(predicate: Callable[[], bool], timeout_s: float = 8.0, step_s: float = 0.003) -> None:
    """Wait until ``predicate()`` is true; raises ``TimeoutError`` after ``timeout_s`` seconds."""

    async def _wait() -> None:
        while not predicate():  # noqa: ASYNC110 - polling arbitrary state, no event to await
            await asyncio.sleep(step_s)

    await asyncio.wait_for(_wait(), timeout_s)
