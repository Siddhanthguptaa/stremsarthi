"""In-process event bus used by tests and by single-process runs."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from typing import Any

from ..enums import NEVER_DROP_TOPICS, Topic
from ..wire import TOPIC_TYPES


class Subscription:
    """Async iterator of ``(topic, event)``. Registered immediately, so nothing published after
    ``subscribe()`` returns is lost. Call ``close()`` (or use ``async with``) when done.

    Bounded: when full, the oldest droppable event is discarded. ``Topic.HOLD`` events are never
    dropped (the queue may briefly exceed ``maxsize`` rather than lose a hold)."""

    def __init__(self, bus: InProcessBus, topics: frozenset[Topic], maxsize: int) -> None:
        self._bus = bus
        self.topics = topics
        self.maxsize = maxsize
        self.dropped = 0
        self._dq: deque[tuple[Topic, Any]] = deque()
        self._evt = asyncio.Event()

    def _push(self, topic: Topic, event: Any) -> None:
        if len(self._dq) >= self.maxsize:
            for i, (t, _) in enumerate(self._dq):
                if t not in NEVER_DROP_TOPICS:
                    del self._dq[i]
                    self.dropped += 1
                    break
        self._dq.append((topic, event))
        self._evt.set()

    def empty(self) -> bool:
        return not self._dq

    def __len__(self) -> int:
        return len(self._dq)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> tuple[Topic, Any]:
        while not self._dq:
            self._evt.clear()
            await self._evt.wait()
        return self._dq.popleft()

    def get_nowait(self) -> tuple[Topic, Any]:
        if not self._dq:
            raise asyncio.QueueEmpty
        return self._dq.popleft()

    def close(self) -> None:
        self._bus._subs.discard(self)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


class InProcessBus:
    def __init__(self, maxsize: int = 1024) -> None:
        self._maxsize = maxsize
        self._subs: set[Subscription] = set()
        self.history: list[tuple[Topic, Any]] = []

    def publish(self, topic: Topic, event: Any) -> None:
        expected = TOPIC_TYPES[topic]
        if not isinstance(event, expected):
            raise TypeError(f"topic {topic.value} carries {expected.__name__}, got {type(event).__name__}")
        self.history.append((topic, event))
        for sub in tuple(self._subs):
            if topic in sub.topics:
                sub._push(topic, event)

    def subscribe(self, topics: Sequence[Topic]) -> Subscription:
        sub = Subscription(self, frozenset(topics), self._maxsize)
        self._subs.add(sub)
        return sub

    def of(self, topic: Topic) -> list[Any]:
        return [e for t, e in self.history if t == topic]
