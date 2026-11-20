"""The API process's own view of chat: hidden count, a snapshot of the moderator queue, a short overlay ring.

The chat process (``chat/``) owns the real moderator queue. The API cannot call it, so it rebuilds what a
late-connecting console or overlay needs from the ``chat.*`` events it already receives on the bus:

* ``chat.verdict`` -> ``hidden_count``. Same rule as the chat process: a message counts as hidden while its
  *latest* verdict is not ALLOW (a later ALLOW, a downgrade or a moderator approval, takes it back out; a
  provisional ``pending_llm`` hide counts). Only ``(platform, message_id)`` keys are tracked, never text.
  The latest ``platform_result`` of a queued message is remembered too (the console's result badge).
* ``chat.modqueue`` -> the live queue. An item is stored while it is pending and upserted on every
  republish. When it is resolved (approved, deleted, timed out, dismissed) its ``ModQueueItem`` (the only
  place the text lives) is dropped at once and a text-free stub stays for a while, so a late action on it
  still gets an exact answer (``not_pending``) instead of a vague one.
* ``chat.allowed`` -> a small ring of messages the streamer was already shown (ALLOW only), so a refreshed
  overlay is not empty.

Privacy (docs/specs/chat.md 5.6, app-api.md 5.3):

* Text of a message whose verdict is not ALLOW exists only inside a pending ``ModQueueItem`` here, in RAM,
  for at most ``ttl_s`` (default 1800 s) and ``max_items`` (default 500), like the chat process. It is never
  logged, written to disk, put in an exception or sent to the streamer role (the hub's topic ACL and the
  streamer REST view enforce that; this class has no method that returns it to a streamer).
* Nothing here is persisted. Log lines carry counts only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from contracts import ChatAction, ChatMessage, ChatVerdict, ModQueueItem, Topic, to_wire

log = logging.getLogger("streamsarthi.api.chat")

Key = tuple[str, str]

DEFAULT_MAX_ITEMS = 500
DEFAULT_TTL_S = 1800.0
ALLOWED_RING = 50  # messages the streamer already saw, kept for a refreshed overlay
RESOLVED_STUBS = 200
TRACK_MAX = 20_000  # latest-verdict map, same bound as the chat process
REQUEST_BUSY_S = 10.0  # a second action on the same message within this window is refused as ``busy``
REPLAY_MAX = 200  # most items a late console is sent over the WebSocket
SWEEP_INTERVAL_S = 30.0
STAFF_ONLY_APPROVE = (
    "approve",
    "dismiss",
)  # delete and timeout are refused for broadcaster/moderator messages


@dataclass(slots=True)
class _Entry:
    key: Key
    categories: tuple[str, ...]
    state: str
    platform_result: str
    seen_at: float  # monotonic time of the last upsert, drives the TTL
    item: ModQueueItem | None  # None once resolved: the text is gone
    is_staff: bool = False
    requested: tuple[str, float] | None = None  # (action, monotonic time) of the last accepted action


class ActionRefused(Exception):
    """A moderator action that cannot be accepted. ``code`` becomes the API error code."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail


def _key(platform: str, message_id: str) -> Key:
    return (platform, message_id)


class ChatState:
    def __init__(
        self,
        *,
        max_items: int = DEFAULT_MAX_ITEMS,
        ttl_s: float = DEFAULT_TTL_S,
        allowed_ring: int = ALLOWED_RING,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_items = max_items
        self.ttl_s = ttl_s
        self._clock = clock
        self._hidden: OrderedDict[Key, bool] = OrderedDict()
        self._hidden_count = 0
        self._entries: OrderedDict[Key, _Entry] = OrderedDict()
        self._allowed: deque[ChatMessage] = deque(maxlen=allowed_ring)
        self._allowed_keys: set[Key] = set()
        self.evicted = 0
        self.expired = 0

    def __repr__(self) -> str:  # never anything about content
        return f"ChatState(hidden={self._hidden_count}, queued={len(self._entries)})"

    # --- configuration ---------------------------------------------------------------------
    def configure(self, *, max_items: int, ttl_s: float) -> None:
        self.max_items = max_items
        self.ttl_s = ttl_s
        self._enforce_cap()

    def reset_overlay(self) -> None:
        """A new stream starts: the hidden counter and the overlay ring begin again. The moderator queue
        belongs to the chat process and is left alone."""
        self._hidden.clear()
        self._hidden_count = 0
        self._allowed.clear()
        self._allowed_keys.clear()

    # --- feeding from the bus --------------------------------------------------------------
    def observe(self, topic: Topic, event: Any) -> None:
        if topic is Topic.CHAT_VERDICT and isinstance(event, ChatVerdict):
            self._on_verdict(event)
        elif topic is Topic.CHAT_MODQUEUE and isinstance(event, ModQueueItem):
            self._on_item(event)
        elif topic is Topic.CHAT_ALLOWED and isinstance(event, ChatMessage):
            self._on_allowed(event)

    def _on_verdict(self, verdict: ChatVerdict) -> None:
        key = _key(verdict.platform, verdict.message_id)
        hidden = verdict.action is not ChatAction.ALLOW
        was = self._hidden.pop(key, False)
        self._hidden[key] = hidden
        while len(self._hidden) > TRACK_MAX:
            self._hidden.popitem(last=False)
        self._hidden_count += int(hidden) - int(was)
        entry = self._entries.get(key)
        if entry is not None:
            entry.platform_result = verdict.platform_result
            entry.requested = None  # the chat process answered: the console may act again

    def _on_item(self, item: ModQueueItem) -> None:
        now = self._clock()
        key = _key(item.message.platform, item.message.message_id)
        old = self._entries.pop(key, None)
        result = item.verdict.platform_result
        if old is not None and old.platform_result != "pending":
            result = old.platform_result  # a verdict newer than the item's own copy of it
        pending = item.state == "pending"
        entry = _Entry(
            key=key,
            categories=tuple(c.value for c in item.verdict.categories),
            state=item.state,
            platform_result=result,
            seen_at=now,
            item=item if pending else None,  # resolved: the text is dropped right here
            is_staff=item.message.is_broadcaster or item.message.is_moderator,
        )
        if pending and old is not None and old.requested is not None:
            entry.requested = old.requested
        self._entries[key] = entry
        self._sweep(now)
        self._enforce_cap()

    def _on_allowed(self, message: ChatMessage) -> None:
        key = _key(message.platform, message.message_id)
        if key in self._allowed_keys:
            return
        if len(self._allowed) == self._allowed.maxlen and self._allowed:
            old = self._allowed[0]
            self._allowed_keys.discard(_key(old.platform, old.message_id))
        self._allowed.append(message)
        self._allowed_keys.add(key)

    # --- housekeeping ----------------------------------------------------------------------
    def _sweep(self, now: float) -> int:
        dead = [k for k, e in self._entries.items() if now - e.seen_at > self.ttl_s]
        for k in dead:
            del self._entries[k]
        self.expired += len(dead)
        return len(dead)

    def sweep(self) -> int:
        return self._sweep(self._clock())

    def _enforce_cap(self) -> None:
        pending = [k for k, e in self._entries.items() if e.item is not None]
        for k in pending[: max(0, len(pending) - self.max_items)]:  # oldest pending first
            del self._entries[k]
            self.evicted += 1
        stubs = [k for k, e in self._entries.items() if e.item is None]
        for k in stubs[: max(0, len(stubs) - RESOLVED_STUBS)]:
            del self._entries[k]

    async def run_sweeper(self, interval_s: float = SWEEP_INTERVAL_S) -> None:
        """Drop expired items on a timer, so text does not outlive its TTL while nobody is looking."""
        while True:
            await asyncio.sleep(interval_s)
            self.sweep()

    # --- reading ---------------------------------------------------------------------------
    @property
    def hidden_count(self) -> int:
        return self._hidden_count

    def pending_count(self) -> int:
        self.sweep()
        return sum(1 for e in self._entries.values() if e.item is not None)

    def pending_items(self) -> list[ModQueueItem]:
        """Pending items, oldest first, each carrying the latest known ``platform_result``."""
        self.sweep()
        out: list[ModQueueItem] = []
        for e in self._entries.values():
            if e.item is None:
                continue
            verdict = e.item.verdict
            if verdict.platform_result != e.platform_result:
                verdict = dataclasses.replace(verdict, platform_result=e.platform_result)  # type: ignore[arg-type]
            out.append(dataclasses.replace(e.item, verdict=verdict))
        return out

    def resolved_stubs(self) -> list[dict[str, Any]]:
        self.sweep()
        return [
            {
                "platform": e.key[0],
                "message_id": e.key[1],
                "state": e.state,
                "categories": list(e.categories),
                "platform_result": e.platform_result,
            }
            for e in self._entries.values()
            if e.item is None
        ]

    def requested(self, platform: str, message_id: str) -> str | None:
        e = self._entries.get(_key(platform, message_id))
        return e.requested[0] if e is not None and e.requested is not None else None

    def mod_snapshot(self, as_of_seq: int) -> dict[str, Any]:
        """The moderator console's REST view. Items carry text: moderator role only (the route enforces it)."""
        items = self.pending_items()
        return {
            "as_of_seq": as_of_seq,
            "items": [to_wire(i) for i in items],
            "resolved": self.resolved_stubs(),
            "pending_count": len(items),
            "hidden_count": self._hidden_count,
            "ttl_s": int(self.ttl_s),
        }

    def overlay_snapshot(self) -> dict[str, Any]:
        """The streamer's REST view: the hidden count and messages that were already allowed. No hidden text."""
        return {
            "hidden_count": self._hidden_count,
            "messages": [
                {
                    "platform": m.platform,
                    "message_id": m.message_id,
                    "author_display": m.author_display,
                    "text": m.text,
                    "received_at": m.received_at,
                    "is_broadcaster": m.is_broadcaster,
                    "is_moderator": m.is_moderator,
                }
                for m in self._allowed
            ],
        }

    def allowed_recent(self) -> list[ChatMessage]:
        return list(self._allowed)

    # --- moderator actions -----------------------------------------------------------------
    def check_action(self, platform: str, message_id: str, action: str) -> None:
        """Raise ``ActionRefused`` unless the action may be sent to the chat process."""
        self.sweep()
        entry = self._entries.get(_key(platform, message_id))
        if entry is None:
            raise ActionRefused(404, "not_found", "no such message in the queue (it may have expired)")
        if entry.item is None:
            raise ActionRefused(409, "not_pending", f"already {entry.state}")
        if entry.is_staff and action not in STAFF_ONLY_APPROVE:
            raise ActionRefused(
                409, "staff_message", "messages from the broadcaster or a moderator are never removed"
            )
        if entry.requested is not None and self._clock() - entry.requested[1] < REQUEST_BUSY_S:
            raise ActionRefused(409, "busy", f"{entry.requested[0]} is already being applied")

    def mark_requested(self, platform: str, message_id: str, action: str) -> None:
        entry = self._entries.get(_key(platform, message_id))
        if entry is not None:
            entry.requested = (action, self._clock())
