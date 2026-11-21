"""The engine's event bus: ``LocalBus`` (in-process hot path) and ``ZmqBroker`` (cross-process bridge).

New code (docs/specs/engine.md 5.9, D-2, D-35); the payload checks and drop-oldest policy follow
``contracts.fakes.InProcessBus``, but this bus is thread-safe (modules publish from their own threads).

``LocalBus``
    * ``publish`` type-checks the payload against ``contracts.TOPIC_TYPES``, never blocks and never
      raises for a slow consumer. Direct handlers (``on``) run in the publisher's thread: that is
      how a hold reaches the engine with no queue in between.
    * ``subscribe`` returns a bounded queue that drops its oldest droppable event and counts it.
      ``Topic.HOLD`` (``NEVER_DROP_TOPICS``) is never dropped. A subscription is both a thread
      consumer (``get``/``drain``) and an ``async`` iterator, so it satisfies ``contracts.EventBus``.

``ZmqBroker``
    One thread runs an XSUB/XPUB proxy. Publishers connect to the XSUB side
    (``ipc:///tmp/streamsarthi-bus-in``), subscribers to the XPUB side (``ipc:///tmp/streamsarthi-bus``).
    Only ``contracts.WIRE_TOPICS`` are ever bridged; a message for any other topic is rejected and
    counted. Events published on the ``LocalBus`` (not those that came from the wire) are sent out
    of the XPUB socket directly as ``[topic, envelope, json]``; the envelope frame is an extra
    ``epoch:seq`` that clients ignore (they read the first and the last frame). Control topics
    (``control.hold``, ``control.config``, ``control.allowlist``, ``control.session``) that arrive
    from a process are decoded with ``contracts.wire.loads`` and delivered to the ``LocalBus`` too.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from contracts import (
    MAX_WIRE_BYTES,
    NEVER_DROP_TOPICS,
    TOPIC_TYPES,
    WIRE_TOPICS,
    ContractError,
    Topic,
    dumps,
    loads,
)

log = logging.getLogger("streamsarthi.engine.bus")

DEFAULT_XPUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus"  # subscribers connect here (D-35)
DEFAULT_XSUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus-in"  # publishers connect here (D-35)
INBOUND_TOPICS: frozenset[Topic] = frozenset({Topic.HOLD, Topic.CONFIG, Topic.ALLOWLIST, Topic.SESSION})

Handler = Callable[[Any], None]
Tap = Callable[[Topic, Any], None]


class LocalSubscription:
    """Bounded drop-oldest queue of ``(topic, event)``; consume from a thread or with ``async for``."""

    def __init__(self, bus: LocalBus, topics: frozenset[Topic], maxsize: int) -> None:
        self._bus = bus
        self.topics = topics
        self.maxsize = maxsize
        self.dropped = 0
        self._dq: deque[tuple[Topic, Any]] = deque()
        self._cv = threading.Condition()
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []
        self._closed = False

    def _push(self, topic: Topic, event: Any) -> None:
        with self._cv:
            if self._closed:
                return
            if len(self._dq) >= self.maxsize:
                for i, (queued, _) in enumerate(self._dq):
                    if queued not in NEVER_DROP_TOPICS:
                        del self._dq[i]
                        self.dropped += 1
                        break
            self._dq.append((topic, event))
            self._cv.notify()
            waiters, self._waiters = self._waiters, []
        for loop, fut in waiters:
            with contextlib.suppress(RuntimeError):  # loop already closed
                loop.call_soon_threadsafe(_resolve, fut)

    def __len__(self) -> int:
        return len(self._dq)

    def empty(self) -> bool:
        return not self._dq

    def get(self, timeout: float | None = None) -> tuple[Topic, Any] | None:
        """Block up to ``timeout`` seconds for the next event; None on timeout or after close."""
        with self._cv:
            if not self._dq and not self._closed:
                self._cv.wait(timeout)
            return self._dq.popleft() if self._dq else None

    def get_nowait(self) -> tuple[Topic, Any] | None:
        with self._cv:
            return self._dq.popleft() if self._dq else None

    def drain(self) -> list[tuple[Topic, Any]]:
        with self._cv:
            items = list(self._dq)
            self._dq.clear()
            return items

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
            waiters, self._waiters = self._waiters, []
        self._bus._remove(self)
        for loop, fut in waiters:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_resolve, fut)

    def __aiter__(self) -> LocalSubscription:
        return self

    async def __anext__(self) -> tuple[Topic, Any]:
        loop = asyncio.get_running_loop()
        while True:
            with self._cv:
                if self._dq:
                    return self._dq.popleft()
                if self._closed:
                    raise StopAsyncIteration
                fut: asyncio.Future[None] = loop.create_future()
                self._waiters.append((loop, fut))
            try:
                await fut
            finally:
                with self._cv:
                    self._waiters = [w for w in self._waiters if w[1] is not fut]

    async def __aenter__(self) -> LocalSubscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


def _resolve(fut: asyncio.Future[None]) -> None:
    if not fut.done():
        fut.set_result(None)


class LocalBus:
    """Thread-safe typed bus. Implements ``contracts.EventBus``."""

    def __init__(self, maxsize: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("queue size must be positive")
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._subs: tuple[LocalSubscription, ...] = ()
        self._handlers: dict[Topic, tuple[Handler, ...]] = {}
        self._taps: tuple[Tap, ...] = ()
        self.published: dict[Topic, int] = {}
        self.handler_errors = 0

    # ------------------------------------------------------------------ publish

    def publish(self, topic: Topic, event: Any) -> None:
        """Deliver to handlers, queues and taps (taps feed the ZeroMQ bridge). Never blocks."""
        self._deliver(topic, event)
        for tap in self._taps:
            self._call(tap, topic, event)

    def publish_remote(self, topic: Topic, event: Any) -> None:
        """Deliver an event that came from another process: not echoed back to the taps."""
        self._deliver(topic, event)

    def _deliver(self, topic: Topic, event: Any) -> None:
        expected = TOPIC_TYPES[Topic(topic)]
        if not isinstance(event, expected):
            raise TypeError(
                f"topic {Topic(topic).value} carries {expected.__name__}, got {type(event).__name__}"
            )
        self.published[topic] = self.published.get(topic, 0) + 1
        for handler in self._handlers.get(topic, ()):
            self._call(handler, event)
        for sub in self._subs:
            if topic in sub.topics:
                sub._push(topic, event)

    def _call(self, fn: Callable[..., None], *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:  # a consumer bug must never reach the publisher (the frame path)
            self.handler_errors += 1
            log.warning("bus handler failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ consume

    def subscribe(self, topics: Sequence[Topic], maxsize: int | None = None) -> LocalSubscription:
        sub = LocalSubscription(self, frozenset(Topic(t) for t in topics), maxsize or self._maxsize)
        with self._lock:
            self._subs = (*self._subs, sub)
        return sub

    def on(self, topic: Topic, handler: Handler) -> Callable[[], None]:
        """Run ``handler(event)`` in the publisher's thread for every event on ``topic``."""
        topic = Topic(topic)
        with self._lock:
            self._handlers[topic] = (*self._handlers.get(topic, ()), handler)

        def remove() -> None:
            with self._lock:
                self._handlers[topic] = tuple(h for h in self._handlers.get(topic, ()) if h is not handler)

        return remove

    def add_tap(self, tap: Tap) -> Callable[[], None]:
        """Observe every locally published event (used by the ZeroMQ bridge)."""
        with self._lock:
            self._taps = (*self._taps, tap)

        def remove() -> None:
            with self._lock:
                self._taps = tuple(t for t in self._taps if t is not tap)

        return remove

    def _remove(self, sub: LocalSubscription) -> None:
        with self._lock:
            self._subs = tuple(s for s in self._subs if s is not sub)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "published": {t.value: n for t, n in self.published.items()},
            "dropped": sum(s.dropped for s in self._subs),
            "handler_errors": self.handler_errors,
            "subscribers": len(self._subs),
        }


FINAL_FLUSH_S = 1.5  # bound on draining the outbound queue at shutdown
FINAL_LINGER_S = 1.0  # bound on ZeroMQ delivering what was sent, before the context closes


class BrokerError(RuntimeError):
    """The ZeroMQ broker could not start (endpoint in use, pyzmq missing)."""


class ZmqBroker:
    """XSUB/XPUB proxy thread that bridges ``WIRE_TOPICS`` between processes and a ``LocalBus``."""

    def __init__(
        self,
        bus: LocalBus,
        *,
        xpub_endpoint: str = DEFAULT_XPUB_ENDPOINT,
        xsub_endpoint: str = DEFAULT_XSUB_ENDPOINT,
        inbound: Iterable[Topic] = INBOUND_TOPICS,
        poll_ms: int = 5,
        queue_max: int = 8192,
    ) -> None:
        self.bus = bus
        self.xpub_endpoint = xpub_endpoint
        self.xsub_endpoint = xsub_endpoint
        self.inbound = frozenset(Topic(t) for t in inbound)
        bad = self.inbound - set(WIRE_TOPICS)
        if bad:
            raise ValueError(f"not wire topics: {sorted(t.value for t in bad)}")
        self._poll_ms = poll_ms
        self._out: deque[tuple[bytes, bytes]] = deque(maxlen=queue_max)
        self._subscribed: set[bytes] = set()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_error: BaseException | None = None
        self._remove_tap: Callable[[], None] | None = None
        self._epoch = int(time.time())
        self._seq = 0
        self.forwarded_out = self.forwarded_in = self.rejected = self.out_dropped = 0

    # ------------------------------------------------------------------ lifecycle

    def start(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            return
        try:
            import zmq  # noqa: F401
        except ImportError as exc:
            raise BrokerError("pyzmq is not installed") from exc
        self._thread = threading.Thread(target=self._run, name="engine-zmq-broker", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise BrokerError("broker did not start in time")
        if self._start_error is not None:
            raise BrokerError(
                f"cannot bind {self.xpub_endpoint} / {self.xsub_endpoint}"
            ) from self._start_error
        self._remove_tap = self.bus.add_tap(self._on_local)

    def stop(self) -> None:
        if self._remove_tap is not None:
            self._remove_tap()
            self._remove_tap = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, int]:
        return {
            "forwarded_out": self.forwarded_out,
            "forwarded_in": self.forwarded_in,
            "rejected": self.rejected,
            "out_dropped": self.out_dropped,
        }

    # ------------------------------------------------------------------ local -> wire

    def _on_local(self, topic: Topic, event: Any) -> None:
        """Bus tap (publisher's thread): queue an event for the XPUB socket if anyone listens."""
        if topic not in WIRE_TOPICS:
            return  # local-only topics never leave the process
        name = topic.value.encode("ascii")
        if not any(name.startswith(prefix) for prefix in tuple(self._subscribed)):
            return
        try:
            payload = dumps(event)
        except (ContractError, TypeError):
            self.rejected += 1
            return
        if len(self._out) == self._out.maxlen:
            self.out_dropped += 1
        self._out.append((name, payload))

    # ------------------------------------------------------------------ the broker thread

    def _run(self) -> None:
        import zmq

        ctx = zmq.Context()
        xsub = ctx.socket(zmq.XSUB)
        xpub = ctx.socket(zmq.XPUB)
        try:
            for sock in (xsub, xpub):
                sock.setsockopt(zmq.LINGER, 0)
                sock.setsockopt(zmq.SNDHWM, 5000)
                sock.setsockopt(zmq.RCVHWM, 5000)
            xsub.bind(self.xsub_endpoint)
            xpub.bind(self.xpub_endpoint)
            for endpoint in (self.xsub_endpoint, self.xpub_endpoint):
                if endpoint.startswith("ipc://"):
                    with contextlib.suppress(OSError):
                        os.chmod(endpoint[len("ipc://") :], 0o600)  # same user only
        except BaseException as exc:
            self._start_error = exc
            xsub.close(0)
            xpub.close(0)
            ctx.term()
            self._ready.set()
            return
        for topic in self.inbound:
            # A PUB socket only sends what its peer subscribed to. The engine is the consumer of the
            # control topics, so it subscribes upstream itself: a process that only publishes
            # (the API's config changes, a hotkey helper) is heard even when nobody else listens.
            xsub.send(b"" + topic.value.encode("ascii"))
        poller = zmq.Poller()
        poller.register(xsub, zmq.POLLIN)
        poller.register(xpub, zmq.POLLIN)
        self._ready.set()
        try:
            while not self._stop.is_set():
                events = dict(poller.poll(self._poll_ms))
                if xsub in events:
                    for _ in range(256):
                        try:
                            frames = xsub.recv_multipart(zmq.NOBLOCK)
                        except zmq.Again:
                            break
                        self._from_publisher(xpub, frames)
                if xpub in events:
                    for _ in range(256):
                        try:
                            message = xpub.recv(zmq.NOBLOCK)
                        except zmq.Again:
                            break
                        self._from_subscriber(xsub, message)
                self._flush_out(xpub)
        except Exception as exc:  # keep the engine alive: it holds and fail-safes without the bridge
            log.error("zmq broker stopped: %s", type(exc).__name__)
        finally:
            # Events queued while stopping (the session's last held / track-close SafetyEvents) must still
            # leave: drain the outbound queue (bounded wait), then let ZeroMQ finish sending (bounded linger).
            try:
                self._final_flush(xpub)
                xpub.setsockopt(zmq.LINGER, int(FINAL_LINGER_S * 1000))
                xpub.close()
            except Exception as exc:  # shutting down: never raise
                log.warning("zmq final flush failed: %s", type(exc).__name__)
                xpub.close(0)
            xsub.close(0)
            ctx.term()  # waits for the XPUB linger above, at most FINAL_LINGER_S

    def _final_flush(self, xpub: Any, timeout_s: float = FINAL_FLUSH_S) -> int:
        """Send everything still queued for the wire, retrying a full socket until ``timeout_s``.
        Returns how many messages were left unsent (dropped and counted)."""
        import zmq

        deadline = time.monotonic() + timeout_s
        while self._out:
            item = self._out.popleft()
            name, payload = item
            self._seq += 1
            try:
                xpub.send_multipart(
                    [name, f"{self._epoch}:{self._seq}".encode("ascii"), payload], zmq.NOBLOCK
                )
                self.forwarded_out += 1
            except zmq.Again:
                self._seq -= 1
                self._out.appendleft(item)
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.005)
        left = len(self._out)
        if left:
            self.out_dropped += left
            self._out.clear()
        return left

    def _from_subscriber(self, xsub: Any, message: bytes) -> None:
        if not message:
            return
        prefix = message[1:]
        if message[0] == 1:
            self._subscribed.add(prefix)
        elif message[0] == 0:
            self._subscribed.discard(prefix)
        xsub.send(message)  # tell connected publishers (their PUB sockets filter on it)

    def _from_publisher(self, xpub: Any, frames: list[bytes]) -> None:
        import zmq

        try:
            topic = Topic(frames[0].decode("ascii"))
        except (IndexError, UnicodeDecodeError, ValueError):
            self.rejected += 1
            return
        if topic not in WIRE_TOPICS or len(frames) < 2 or len(frames[-1]) > MAX_WIRE_BYTES:
            self.rejected += 1  # local-only or unknown topic, or an oversized message
            return
        if topic in self.inbound:
            try:
                event = loads(frames[-1])
            except ContractError:
                self.rejected += 1
                return
            if not isinstance(event, TOPIC_TYPES[topic]):
                self.rejected += 1
                return
            self.bus.publish_remote(topic, event)
        self.forwarded_in += 1
        try:
            xpub.send_multipart(frames, zmq.NOBLOCK)
        except zmq.Again:
            self.out_dropped += 1

    def _flush_out(self, xpub: Any) -> None:
        import zmq

        while self._out:
            name, payload = self._out.popleft()
            self._seq += 1
            try:
                xpub.send_multipart(
                    [name, f"{self._epoch}:{self._seq}".encode("ascii"), payload], zmq.NOBLOCK
                )
                self.forwarded_out += 1
            except zmq.Again:
                self.out_dropped += 1
