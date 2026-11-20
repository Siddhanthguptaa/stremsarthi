"""RAM ring of ``SafetyEvent`` keyed by ``event_id`` (upsert), with filtered, paginated reads.

The engine publishes an event when a mask starts and republishes the same ``event_id`` with
``duration_ms`` when the track ends, so a write is an upsert: the newer event replaces the older one,
counts once, and moves to the end of the ring (it gets a new cursor, so a client polling with
``cursor`` also sees the update).

Privacy: ``SafetyEvent`` carries category, time, confidence, a box and fixed codes, never text or
images (the contract enforces it). Nothing else is stored here, and only ``SafetyEvent`` is accepted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from contracts import SafetyEvent, Topic

from .bus import BusClient, Subscription

log = logging.getLogger("streamsarthi.api.events")

DEFAULT_CAPACITY = 5000
MAX_PAGE = 1000


def parse_ts(value: str) -> float | None:
    """ISO-8601 -> epoch seconds (naive times are read as UTC); None when it does not parse."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


@dataclass(slots=True)
class _Entry:
    seq: int
    ts: float
    event: SafetyEvent


class SafetyEventStore:
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self.capacity = capacity
        self._by_id: OrderedDict[str, _Entry] = OrderedDict()
        self._seq = 0

    def __len__(self) -> int:
        return len(self._by_id)

    @property
    def last_cursor(self) -> int:
        return self._seq

    def upsert(self, event: SafetyEvent) -> bool:
        """Store or replace by ``event_id``. Returns True when the id is new."""
        if not isinstance(event, SafetyEvent):
            raise TypeError("only SafetyEvent is stored")
        self._seq += 1
        ts = parse_ts(event.wall_ts)
        old = self._by_id.pop(event.event_id, None)
        self._by_id[event.event_id] = _Entry(self._seq, ts if ts is not None else time.time(), event)
        while len(self._by_id) > self.capacity:
            self._by_id.popitem(last=False)
        return old is None

    def _matching(
        self,
        modalities: frozenset[str] | None,
        categories: frozenset[str] | None,
        since: float | None,
        until: float | None,
        session_id: str | None,
    ) -> Iterable[_Entry]:
        for entry in self._by_id.values():
            e = entry.event
            if modalities is not None and e.modality.value not in modalities:
                continue
            if categories is not None and e.category.value not in categories:
                continue
            if since is not None and entry.ts < since:
                continue
            if until is not None and entry.ts > until:
                continue
            if session_id is not None and e.session_id != session_id:
                continue
            yield entry

    def query(
        self,
        *,
        modalities: Iterable[str] | None = None,
        categories: Iterable[str] | None = None,
        since: float | None = None,
        until: float | None = None,
        session_id: str | None = None,
        cursor: int = 0,
        limit: int = 200,
        newest_first: bool = False,
    ) -> tuple[list[SafetyEvent], int]:
        """Return ``(events, next_cursor)``.

        Ascending (default): events whose cursor is greater than ``cursor``, oldest first; pass
        ``next_cursor`` back to continue. ``newest_first`` returns the latest ``limit`` events,
        newest first (to fill a feed); ``next_cursor`` is then the ring's last cursor.
        """
        limit = max(1, min(limit, MAX_PAGE))
        hits = list(
            self._matching(
                None if modalities is None else frozenset(modalities),
                None if categories is None else frozenset(categories),
                since,
                until,
                session_id,
            )
        )
        if newest_first:
            page = hits[-limit:][::-1]
            return [h.event for h in page], self._seq
        page = [h for h in hits if h.seq > cursor][:limit]
        return [h.event for h in page], (page[-1].seq if page else cursor)

    def summary(
        self,
        *,
        modalities: Iterable[str] | None = None,
        categories: Iterable[str] | None = None,
        since: float | None = None,
        until: float | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Counts only: per category (count, mean confidence), per action and per modality."""
        by_cat: dict[str, list[float]] = {}
        by_action: dict[str, int] = {}
        by_modality: dict[str, int] = {}
        first: float | None = None
        last: float | None = None
        total = 0
        for entry in self._matching(
            None if modalities is None else frozenset(modalities),
            None if categories is None else frozenset(categories),
            since,
            until,
            session_id,
        ):
            e = entry.event
            total += 1
            by_cat.setdefault(e.category.value, []).append(e.confidence)
            by_action[e.action] = by_action.get(e.action, 0) + 1
            by_modality[e.modality.value] = by_modality.get(e.modality.value, 0) + 1
            first = entry.ts if first is None else min(first, entry.ts)
            last = entry.ts if last is None else max(last, entry.ts)

        def iso(epoch: float | None) -> str | None:
            return (
                None if epoch is None else datetime.fromtimestamp(epoch, tz=UTC).isoformat(timespec="seconds")
            )

        return {
            "total": total,
            "by_category": {
                c: {"count": len(v), "mean_confidence": round(sum(v) / len(v), 3)}
                for c, v in sorted(by_cat.items())
            },
            "by_action": dict(sorted(by_action.items())),
            "by_modality": dict(sorted(by_modality.items())),
            "first_ts": iso(first),
            "last_ts": iso(last),
        }

    @staticmethod
    def subscribe(bus: BusClient) -> Subscription:
        """Subscribe before the first event can arrive (call from start-up, then ``run``)."""
        return bus.subscribe([Topic.SAFETY_EVENT])

    async def run(self, sub: Subscription) -> None:
        """Feed the store from a ``safety.event`` subscription until cancelled."""
        try:
            async for _topic, event in sub:
                try:
                    self.upsert(event)
                except TypeError:
                    log.warning("ignored a non-SafetyEvent on safety.event")
        except asyncio.CancelledError:
            raise
        finally:
            sub.close()
