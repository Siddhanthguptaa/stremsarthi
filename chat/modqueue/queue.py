"""Moderator queue backend (docs/specs/chat.md 5.5): RAM only, capped, expiring.

* ``add`` stores a flagged message with its verdict and publishes the ``ModQueueItem`` on ``chat.modqueue``
  (text included, moderator role only: the API enforces that).
* ``handle(ModAction)`` runs a moderator's decision:
  ``approve`` releases the message to the overlay (once; a message already shown is not shown twice),
  ``delete`` / ``timeout`` call the platform through the dispatcher and report the result as
  ``platform_result`` on a follow-up ``ChatVerdict``, ``dismiss`` closes the item and keeps it hidden.
  Every state change republishes the item.
* A platform action that fails leaves the item pending, so the moderator can retry or dismiss.

Nothing here logs message text; log lines carry a hashed id, the action and a result code.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from chat.common import ActionResult, ChatPublisher, Clock, SystemClock, hash_id, log_event
from contracts import ChatAction, ChatMessage, ChatVerdict, EventBus, ModAction, ModQueueItem, Topic
from contracts.config import ModQueueConfig

from .store import MemoryStore, key_of

_LOG = logging.getLogger("chat.modqueue")
ItemState = Literal["pending", "approved", "deleted", "timed_out", "dismissed"]
OutcomeStatus = Literal["ok", "unknown", "not_pending", "busy", "unsupported", "failed"]


class ModActionBackend(Protocol):
    """Satisfied by ``ActionDispatcher`` (moderator calls: no policy gate, no downgrade)."""

    async def delete(self, message: ChatMessage) -> ActionResult: ...

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult: ...


@dataclass(frozen=True, slots=True)
class ModActionOutcome:
    status: OutcomeStatus
    state: ItemState | None = None
    detail: str = ""


class ModQueue:
    def __init__(
        self,
        bus: EventBus,
        backend: ModActionBackend,
        release: Callable[[ChatMessage], bool],
        publisher: ChatPublisher,
        config: ModQueueConfig | None = None,
        *,
        clock: Clock | None = None,
        on_event: Callable[[str], None] | None = None,
        on_verdict: Callable[[ChatVerdict], None] | None = None,
    ) -> None:
        cfg = config or ModQueueConfig()
        self._bus = bus
        self._backend = backend
        self._release = release
        self._publisher = publisher
        self._clock: Clock = clock or SystemClock()
        self._on_event = on_event
        self._on_verdict = on_verdict
        self._store = MemoryStore(cfg.max_items, cfg.ttl_s, clock=self._clock)
        self._busy: set[tuple[str, str]] = set()
        self.evicted = 0
        self.expired = 0

    def __repr__(self) -> str:
        return f"ModQueue(items={len(self._store)})"

    # ---- reading ------------------------------------------------------------------------------
    def snapshot(self) -> list[ModQueueItem]:
        """Live items, oldest first (for a moderator console that connects late)."""
        self._count_expired(self._store.sweep())
        return self._store.items()

    def pending_count(self) -> int:
        return len(self._store.pending())

    def get(self, platform: str, message_id: str) -> ModQueueItem | None:
        return self._store.get(platform, message_id)

    def __len__(self) -> int:
        return len(self._store)

    def configure(self, config: ModQueueConfig) -> None:
        self._count_evicted(self._store.configure(config.max_items, config.ttl_s))

    def sweep(self) -> int:
        expired = self._store.sweep()
        self._count_expired(expired)
        return len(expired)

    # ---- writing ------------------------------------------------------------------------------
    def add(self, message: ChatMessage, verdict: ChatVerdict, state: ItemState = "pending") -> ModQueueItem:
        item = ModQueueItem(message, verdict, self._clock.wall(), state)
        self._count_evicted(self._store.put(item))
        self._publish(item)
        return item

    async def handle(self, action: ModAction) -> ModActionOutcome:
        key = key_of(action.platform, action.message_id)
        item = self._store.get(action.platform, action.message_id)
        mid = hash_id(action.message_id)
        if item is None:
            return self._done(action, mid, ModActionOutcome("unknown"))
        if item.state != "pending":
            return self._done(action, mid, ModActionOutcome("not_pending", item.state))
        if key in self._busy:
            return self._done(action, mid, ModActionOutcome("busy", item.state))
        self._busy.add(key)
        try:
            outcome = await self._run(action, item)
        finally:
            self._busy.discard(key)
        return self._done(action, mid, outcome)

    # ---- internals ----------------------------------------------------------------------------
    async def _run(self, action: ModAction, item: ModQueueItem) -> ModActionOutcome:
        if action.action == "approve":
            self._release(item.message)
            self._emit(self._followup(item, ChatAction.ALLOW, "mod_approved", "done"))
            return self._close(item, "approved")
        if action.action == "dismiss":
            return self._close(item, "dismissed")
        if action.action == "delete":
            res = await self._backend.delete(item.message)
            self._emit(self._followup(item, ChatAction.DELETE, "mod_delete", res.status))
            return self._after_platform(item, res, "deleted")
        seconds = action.timeout_seconds or 0
        res = await self._backend.timeout(item.message, seconds)
        self._emit(
            self._followup(item, ChatAction.TIMEOUT, "mod_timeout", res.status, timeout_seconds=seconds)
        )
        return self._after_platform(item, res, "timed_out")

    def _after_platform(
        self, item: ModQueueItem, res: ActionResult, done_state: ItemState
    ) -> ModActionOutcome:
        if res.status == "done":
            return self._close(item, done_state)
        return ModActionOutcome(res.status, item.state, res.reason)

    def _close(self, item: ModQueueItem, state: ItemState) -> ModActionOutcome:
        closed = dataclasses.replace(item, state=state)
        self._count_evicted(self._store.put(closed))
        self._publish(closed)
        return ModActionOutcome("ok", state)

    @staticmethod
    def _followup(
        item: ModQueueItem,
        action: ChatAction,
        reason: str,
        platform_result: Literal["pending", "done", "unsupported", "failed"],
        *,
        timeout_seconds: int | None = None,
    ) -> ChatVerdict:
        base = item.verdict
        return ChatVerdict(
            platform=base.platform,
            message_id=base.message_id,
            action=action,
            categories=base.categories,
            confidence=base.confidence,
            reason=reason,
            stage=base.stage,
            latency_ms=0.0,
            timeout_seconds=timeout_seconds,
            platform_result=platform_result,
        )

    def _emit(self, verdict: ChatVerdict) -> None:
        if self._on_verdict is not None:
            self._on_verdict(verdict)
        try:
            self._publisher.verdict(verdict)
        except Exception as exc:
            log_event(_LOG, "modqueue_verdict_failed", level=logging.ERROR, error=type(exc).__name__)

    def _publish(self, item: ModQueueItem) -> None:
        try:
            self._bus.publish(Topic.CHAT_MODQUEUE, item)
        except Exception as exc:  # a bus fault must never stop the chat process
            log_event(_LOG, "modqueue_publish_failed", level=logging.ERROR, error=type(exc).__name__)

    def _count_evicted(self, evicted: list[ModQueueItem]) -> None:
        if evicted:
            self.evicted += len(evicted)
            self._signal("modqueue_full")

    def _count_expired(self, expired: list[ModQueueItem]) -> None:
        self.expired += len(expired)

    def _signal(self, code: str) -> None:
        if self._on_event is not None:
            try:
                self._on_event(code)
            except Exception as exc:
                log_event(_LOG, "modqueue_event_failed", level=logging.ERROR, error=type(exc).__name__)

    @staticmethod
    def _done(action: ModAction, mid: str, outcome: ModActionOutcome) -> ModActionOutcome:
        level = logging.INFO if outcome.status == "ok" else logging.WARNING
        log_event(
            _LOG,
            "mod_action",
            level=level,
            mid=mid,
            act=action.action,
            status=outcome.status,
            detail=outcome.detail or "none",
            actor=action.actor,
        )
        return outcome
