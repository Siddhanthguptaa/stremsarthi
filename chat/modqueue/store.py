"""RAM-only store for the moderator queue (docs/specs/chat.md 5.5).

Nothing is written to disk. Capacity is ``max_items``; an item lives ``ttl_s`` seconds from the moment it
was stored. When the cap is hit the oldest item is evicted, resolved items (approved, deleted, timed out,
dismissed) before pending ones, so a moderator never loses an open item to make room for a closed one.
"""

from __future__ import annotations

from collections import OrderedDict

from chat.common import Clock, SystemClock
from contracts import ModQueueItem

Key = tuple[str, str]


def key_of(platform: str, message_id: str) -> Key:
    return (platform, message_id)


class MemoryStore:
    def __init__(self, max_items: int = 500, ttl_s: float = 1800.0, *, clock: Clock | None = None) -> None:
        self._max = max_items
        self._ttl = ttl_s
        self._clock: Clock = clock or SystemClock()
        self._items: OrderedDict[Key, tuple[ModQueueItem, float]] = OrderedDict()

    def __repr__(self) -> str:  # never print queued text
        return f"MemoryStore(items={len(self._items)})"

    def __len__(self) -> int:
        return len(self._items)

    def configure(self, max_items: int, ttl_s: float) -> list[ModQueueItem]:
        """Apply new limits. Returns the items evicted to fit a smaller cap."""
        self._max, self._ttl = max_items, ttl_s
        return self._evict()

    def put(self, item: ModQueueItem) -> list[ModQueueItem]:
        """Insert or replace (a replaced item keeps its place and its original expiry). Returns the
        items evicted by the cap."""
        k = key_of(item.message.platform, item.message.message_id)
        old = self._items.get(k)
        deadline = old[1] if old is not None else self._clock.monotonic() + self._ttl
        self._items[k] = (item, deadline)
        return self._evict()

    def get(self, platform: str, message_id: str) -> ModQueueItem | None:
        entry = self._items.get(key_of(platform, message_id))
        if entry is None:
            return None
        if entry[1] <= self._clock.monotonic():
            del self._items[key_of(platform, message_id)]
            return None
        return entry[0]

    def items(self) -> list[ModQueueItem]:
        """All live items, oldest first."""
        self.sweep()
        return [item for item, _ in self._items.values()]

    def pending(self) -> list[ModQueueItem]:
        return [item for item in self.items() if item.state == "pending"]

    def sweep(self) -> list[ModQueueItem]:
        """Drop expired items. Returns them."""
        now = self._clock.monotonic()
        expired = [k for k, (_, deadline) in self._items.items() if deadline <= now]
        return [self._items.pop(k)[0] for k in expired]

    def _evict(self) -> list[ModQueueItem]:
        evicted: list[ModQueueItem] = []
        while len(self._items) > self._max:
            victim = next((k for k, (it, _) in self._items.items() if it.state != "pending"), None)
            if victim is None:
                victim = next(iter(self._items))
            evicted.append(self._items.pop(victim)[0])
        return evicted
