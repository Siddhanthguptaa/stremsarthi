"""Chat pipeline orchestrator (T4.5): connector -> command check -> classifier -> route.

``ChatPipeline.submit(message)`` is what a connector calls (it is the ``on_message`` callback of the YouTube
poller, the EventSub transport and the anonymous IRC reader). It never blocks and never raises.

1. ``HoldCommands.handle`` runs FIRST and synchronously, so ``!hold`` bypasses every queue (well under
   100 ms even during a raid). A recognised token is silent: not classified, not shown, not queued.
2. Other messages go into a bounded queue (``max_queue`` = 1,000). On overflow the OLDEST waiting message is
   dropped (it is never shown: unclassified text is never released) and ``chat_overload`` is reported at
   most once per ``overload_event_every_s``.
3. A worker takes the queue in batches, runs ``ChatClassifier.classify`` and hands every verdict to the
   ``ActionsRouter`` (overlay / mod queue / platform actions / safety events). Messages the classifier
   marks ``pending_llm`` are resolved later: the classifier's ``on_update`` callback routes the final
   verdict through the same router (upgrade, platform action, or downgrade to ALLOW which publishes
   ``chat.allowed``).
4. The bus loop applies ``chat.*`` keys of ``ConfigChange`` and executes ``ModAction`` s.

Failure policy: a classifier or routing fault drops the message (hidden), reports a code and continues;
the chat process never takes the stream down. No message text is ever logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from typing import Any

from pydantic import ValidationError

from chat.classify import ChatClassifier, LlmTransport
from chat.commands import HoldCommands
from chat.common import ChatPublisher, Clock, SystemClock, hash_id, log_event
from chat.moderation import ActionDispatcher, PlatformActions
from chat.modqueue import ModActionOutcome, ModQueue
from contracts import ChatMessage, ChatVerdict, ConfigChange, EventBus, ModAction, Topic
from contracts.config import ChatConfig

from .actions_router import ActionsRouter
from .overlay_feed import OverlayFeed

_LOG = logging.getLogger("chat.pipeline")
MAX_QUEUE = 1000
BATCH_MAX = 50
_SEEN_CAP = 5000
_HOUSEKEEPING_S = 10.0


class ChatPipeline:
    def __init__(
        self,
        bus: EventBus,
        config: ChatConfig | None = None,
        *,
        classifier: Any | None = None,
        llm: LlmTransport | None = None,
        youtube: PlatformActions | None = None,
        twitch: PlatformActions | None = None,
        clock: Clock | None = None,
        session_id: str = "chat",
        max_queue: int = MAX_QUEUE,
        batch_max: int = BATCH_MAX,
        overload_event_every_s: float = 5.0,
    ) -> None:
        self._bus = bus
        self._cfg = config or ChatConfig()
        self._clock: Clock = clock or SystemClock()
        self._max_queue = max_queue
        self._batch_max = batch_max
        self._overload_every = overload_event_every_s
        self._youtube = youtube
        self._twitch = twitch

        self.publisher = ChatPublisher(bus, session_id=session_id, clock=self._clock)
        self.commands = HoldCommands(bus, self.publisher, self._cfg.commands, clock=self._clock)
        self.dispatcher = ActionDispatcher(
            youtube=youtube, twitch=twitch, config=self._cfg, publisher=self.publisher
        )
        self.feed = OverlayFeed(self.publisher)
        self.modqueue = ModQueue(
            bus,
            self.dispatcher,
            self.feed.release,
            self.publisher,
            self._cfg.modqueue,
            clock=self._clock,
            on_event=self._queue_event,
            on_verdict=self.feed.note_verdict,
        )
        self.router = ActionsRouter(
            bus, self.publisher, self.feed, self.modqueue, self.dispatcher, session_id=session_id, clock=self._clock
        )  # fmt: skip
        self.classifier = classifier or ChatClassifier(
            self._cfg, llm=llm, clock=self._clock, on_update=self._on_update
        )
        if classifier is not None and hasattr(classifier, "set_update_callback"):
            classifier.set_update_callback(self._on_update)

        self._queue: deque[tuple[ChatMessage, float]] = deque()
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._action_tasks: set[asyncio.Task[Any]] = set()
        self._sub: Any = None
        self._last_overload = float("-inf")
        self._busy = False
        self.latency_ms: deque[float] = deque(maxlen=4096)
        self.counts = {
            "received": 0,
            "commands": 0,
            "duplicates": 0,
            "dropped_overload": 0,
            "classify_errors": 0,
            "config_applied": 0,
            "config_rejected": 0,
            "mod_actions": 0,
        }

    def __repr__(self) -> str:
        return f"ChatPipeline(queued={len(self._queue)})"

    @property
    def config(self) -> ChatConfig:
        return self._cfg

    # ---- connector entry point ----------------------------------------------------------------
    def submit(self, message: ChatMessage) -> None:
        """Never blocks, never raises. Commands are handled here, before any queue."""
        try:
            self.counts["received"] += 1
            if self.commands.handle(message) is not None:
                self.counts["commands"] += 1
                return
            key = (message.platform, message.message_id)
            if key in self._seen:
                self.counts["duplicates"] += 1
                return
            self._seen[key] = None
            while len(self._seen) > _SEEN_CAP:
                self._seen.popitem(last=False)
            if len(self._queue) >= self._max_queue:
                self._queue.popleft()
                self.counts["dropped_overload"] += 1
                self._report_overload()
            self._queue.append((message, self._clock.monotonic()))
            self._wake.set()
        except Exception as exc:
            log_event(_LOG, "submit_failed", level=logging.ERROR, error=type(exc).__name__)

    async def on_message(self, message: ChatMessage) -> None:
        """Awaitable form of ``submit`` for connectors typed ``Awaitable | None``."""
        self.submit(message)

    # ---- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        """Start the worker, housekeeping and bus loops. Subscribes immediately: no event is lost."""
        if self._tasks:
            return
        self._sub = self._bus.subscribe([Topic.CONFIG, Topic.CHAT_MODACTION])
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._worker()),
            loop.create_task(self._bus_loop(self._sub)),
            loop.create_task(self._housekeeping()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._sub is not None and hasattr(self._sub, "close"):
            self._sub.close()
        self._sub = None
        for task in list(self._action_tasks):
            task.cancel()
        await asyncio.gather(*list(self._action_tasks), return_exceptions=True)
        await self.router.aclose()
        aclose = getattr(self.classifier, "aclose", None)
        if aclose is not None:
            await aclose()

    async def idle(self, timeout_s: float = 5.0) -> None:
        """Wait until the queue is drained and platform actions finished (tests, shutdown)."""

        async def wait() -> None:
            while self._queue or self._busy:  # noqa: ASYNC110 - polling plain state
                await asyncio.sleep(0.001)
            await self.router.drain()

        await asyncio.wait_for(wait(), timeout_s)

    # ---- worker -------------------------------------------------------------------------------
    async def _worker(self) -> None:
        while True:
            while not self._queue:
                self._wake.clear()
                if self._queue:
                    break
                await self._wake.wait()
            self._busy = True
            try:
                n = min(len(self._queue), self._batch_max)
                batch = [self._queue.popleft() for _ in range(n)]
                await self._process([m for m, _ in batch], [t for _, t in batch])
            finally:
                self._busy = False
            await asyncio.sleep(0)  # let submit() (hold commands) and the bus loop run between batches

    async def _process(self, messages: list[ChatMessage], t_in: list[float]) -> None:
        try:
            verdicts = list(await self.classifier.classify(messages))
            if len(verdicts) != len(messages):
                raise ValueError("verdict count mismatch")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # fail closed: nothing from this batch is shown
            self.counts["classify_errors"] += len(messages)
            log_event(_LOG, "classify_failed", level=logging.ERROR, n=len(messages), error=type(exc).__name__)
            self.publisher.safety("chat_classify_error", source="chat.pipeline")
            return
        now = self._clock.monotonic()
        for msg, verdict, t0 in zip(messages, verdicts, t_in, strict=True):
            self.latency_ms.append((now - t0) * 1000.0)
            self._route(msg, verdict)

    def _route(self, message: ChatMessage, verdict: ChatVerdict) -> None:
        try:
            self.router.handle(message, verdict)
        except Exception as exc:
            log_event(
                _LOG, "route_failed", level=logging.ERROR, mid=hash_id(message.message_id),
                error=type(exc).__name__,
            )  # fmt: skip

    def _on_update(self, message: ChatMessage, verdict: ChatVerdict) -> None:
        """Final verdict of a provisionally hidden message (called by the classifier)."""
        self._route(message, verdict)

    # ---- bus ----------------------------------------------------------------------------------
    async def _bus_loop(self, sub: Any) -> None:
        async for topic, event in sub:
            try:
                if topic == Topic.CONFIG and isinstance(event, ConfigChange):
                    self.apply_config(event)
                elif topic == Topic.CHAT_MODACTION and isinstance(event, ModAction):
                    task = asyncio.get_running_loop().create_task(self.handle_mod_action(event))
                    self._action_tasks.add(task)
                    task.add_done_callback(self._action_tasks.discard)
            except Exception as exc:
                log_event(_LOG, "bus_event_failed", level=logging.ERROR, error=type(exc).__name__)

    async def handle_mod_action(self, action: ModAction) -> ModActionOutcome:
        self.counts["mod_actions"] += 1
        return await self.modqueue.handle(action)

    async def _housekeeping(self) -> None:
        while True:
            await asyncio.sleep(_HOUSEKEEPING_S)
            self.modqueue.sweep()

    # ---- config -------------------------------------------------------------------------------
    def apply_config(self, change: ConfigChange) -> bool:
        """Apply the ``chat.*`` keys of a ``ConfigChange``. An invalid change is rejected as a whole."""
        updates = {k[len("chat.") :]: v for k, v in change.diff.items() if k.startswith("chat.")}
        if not updates:
            return False
        data = self._cfg.model_dump()
        try:
            for path, value in updates.items():
                node = data
                *parents, leaf = path.split(".")
                for part in parents:
                    node = node[part]
                if leaf not in node:
                    raise KeyError("unknown chat config key")
                node[leaf] = list(value) if isinstance(value, tuple) else value
            new = ChatConfig.model_validate(data)
        except (KeyError, TypeError, ValidationError) as exc:
            self.counts["config_rejected"] += 1
            log_event(_LOG, "config_rejected", level=logging.WARNING, error=type(exc).__name__)
            return False
        self._cfg = new
        update = getattr(self.classifier, "update_config", None)
        if update is not None:
            update(new)
        self.dispatcher.update_config(new)
        self.commands.update_config(new.commands)
        self.modqueue.configure(new.modqueue)
        if self._youtube is not None and hasattr(self._youtube, "allow_timeout"):
            self._youtube.allow_timeout = new.youtube.allow_timeout
        self.counts["config_applied"] += 1
        return True

    # ---- events and stats ---------------------------------------------------------------------
    def _queue_event(self, code: str) -> None:
        self.publisher.safety(code, source="chat.modqueue")

    def _report_overload(self) -> None:
        now = self._clock.monotonic()
        if now - self._last_overload >= self._overload_every:
            self._last_overload = now
            self.publisher.safety("chat_overload", source="chat.pipeline")

    def latency_p95_ms(self) -> float:
        """p95 of submit-to-verdict-published time (rules / provisional path) over the last 4,096 messages."""
        if not self.latency_ms:
            return 0.0
        data = sorted(self.latency_ms)
        return data[min(len(data) - 1, int(0.95 * len(data)))]

    def stats(self) -> dict[str, int | float]:
        out: dict[str, int | float] = dict(self.counts)
        out.update(
            queued=len(self._queue),
            modqueue_pending=self.modqueue.pending_count(),
            hidden_count=self.feed.hidden_count,
            shown=self.feed.state().shown,
            p95_ms=round(self.latency_p95_ms(), 3),
        )
        return out
