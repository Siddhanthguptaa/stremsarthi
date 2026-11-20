"""Route a final or provisional ``ChatVerdict`` to the overlay, the mod queue, the platform and the report.

``ActionsRouter.handle(message, verdict)`` is synchronous for everything local and spawns one task for a
verdict that needs the platform (DELETE / TIMEOUT), so a slow platform call never delays other messages.
It is used for the fast verdict of every message AND for late ``on_update`` verdicts of messages that were
provisionally hidden (``reason="pending_llm"``), so every non-local verdict reaches
``ActionDispatcher.apply`` exactly once.

Per verdict:
* ALLOW: ``chat.allowed`` (once). A staff message with reason ``staff_dox_flag`` is shown, and ALSO queued for
  moderators (a ``warned`` safety event); it is never hidden.
* provisional ``pending_llm`` hide: published on ``chat.verdict`` only. It is not queued and raises no
  safety event; the final verdict does both. (The classifier always finalises within ``llm_pending_max_s``.)
* HIDE_FROM_STREAMER / HOLD_FOR_MOD: queued (state pending), ``hidden`` / ``held`` safety event.
* DELETE / TIMEOUT: ``ActionDispatcher.apply`` (it downgrades what the platform cannot do and publishes the
  follow-up verdict with ``platform_result``), then queued with the verdict as it finally stands
  (state ``deleted`` / ``timed_out`` when the platform did it), and one safety event.

Safety events carry the category, the action and the reason code, never text. Nothing here logs text.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from datetime import UTC, datetime

from chat.common import ChatPublisher, Clock, SystemClock, hash_id, log_event
from chat.moderation import ActionDispatcher
from chat.modqueue import ModQueue
from contracts import ChatAction, ChatMessage, ChatVerdict, EventBus, Modality, SafetyEvent, Topic

from .overlay_feed import OverlayFeed

_LOG = logging.getLogger("chat.router")
PENDING_REASON = "pending_llm"
STAFF_DOX_REASON = "staff_dox_flag"
_MAX_ACTIONS_IN_FLIGHT = 16
_SAFETY_ACTION = {
    ChatAction.HIDE_FROM_STREAMER: "hidden",
    ChatAction.HOLD_FOR_MOD: "held",
    ChatAction.DELETE: "deleted",
    ChatAction.TIMEOUT: "timed_out",
}


class ActionsRouter:
    def __init__(
        self,
        bus: EventBus,
        publisher: ChatPublisher,
        feed: OverlayFeed,
        queue: ModQueue,
        dispatcher: ActionDispatcher,
        *,
        session_id: str = "chat",
        clock: Clock | None = None,
    ) -> None:
        self._bus = bus
        self._publisher = publisher
        self._feed = feed
        self._queue = queue
        self._dispatcher = dispatcher
        self._session_id = session_id
        self._clock: Clock = clock or SystemClock()
        self._sem = asyncio.Semaphore(_MAX_ACTIONS_IN_FLIGHT)
        self._tasks: set[asyncio.Task[None]] = set()
        self.routed = 0
        self.action_errors = 0

    def __repr__(self) -> str:
        return f"ActionsRouter(in_flight={len(self._tasks)})"

    # ---- entry point --------------------------------------------------------------------------
    def handle(self, message: ChatMessage, verdict: ChatVerdict) -> None:
        self.routed += 1
        self._feed.note_verdict(verdict)
        self._publisher.verdict(verdict)
        action = verdict.action
        if action is ChatAction.ALLOW:
            self._feed.release(message)
            if verdict.reason == STAFF_DOX_REASON:
                self._queue.add(message, verdict)
                self._safety(verdict, "warned")
            return
        if verdict.reason == PENDING_REASON:
            return
        if action in (ChatAction.DELETE, ChatAction.TIMEOUT):
            task = asyncio.get_running_loop().create_task(self._act(message, verdict))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        self._queue_and_report(message, verdict)

    async def drain(self) -> None:
        """Wait for in-flight platform actions (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    # ---- internals ----------------------------------------------------------------------------
    async def _act(self, message: ChatMessage, verdict: ChatVerdict) -> None:
        try:
            async with self._sem:
                final = await self._dispatcher.apply(message, verdict)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the dispatcher guards adapters; this is the last line of defence
            self.action_errors += 1
            log_event(
                _LOG, "action_route_failed", level=logging.ERROR, mid=hash_id(message.message_id),
                error=type(exc).__name__,
            )  # fmt: skip
            final = dataclasses.replace(
                verdict,
                action=ChatAction.HOLD_FOR_MOD,
                timeout_seconds=None,
                reason="action_error_hold",
                platform_result="failed",
            )
            self._publisher.verdict(final)
        self._feed.note_verdict(final)
        self._queue_and_report(message, final)

    def _queue_and_report(self, message: ChatMessage, verdict: ChatVerdict) -> None:
        state = "pending"
        if verdict.platform_result == "done":
            if verdict.action is ChatAction.DELETE:
                state = "deleted"
            elif verdict.action is ChatAction.TIMEOUT:
                state = "timed_out"
        self._queue.add(message, verdict, state)  # type: ignore[arg-type]
        self._safety(verdict, _SAFETY_ACTION[verdict.action])

    def _safety(self, verdict: ChatVerdict, action: str) -> None:
        """One ``SafetyEvent`` per message outcome: category, action and reason code only."""
        try:
            event = SafetyEvent(
                event_id=uuid.uuid4().hex,
                session_id=self._session_id,
                wall_ts=datetime.fromtimestamp(self._clock.wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                pts=None,
                modality=Modality.CHAT,
                category=verdict.categories[0],
                action=action,  # type: ignore[arg-type]
                confidence=verdict.confidence,
                bbox=None,
                duration_ms=None,
                source="chat.pipeline",
                detail_code=verdict.reason[:64],
            )
            self._bus.publish(Topic.SAFETY_EVENT, event)
        except Exception as exc:
            log_event(_LOG, "safety_publish_failed", level=logging.ERROR, error=type(exc).__name__)
