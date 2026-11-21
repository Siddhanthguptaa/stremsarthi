"""ZeroMQ ``EventBus`` for the chat process (D-44). Imports only ``contracts`` and the standard library.

Wire format (same as the engine's broker and the API's client; see ``contracts/wire.py``): a message is
``[topic, payload]`` where ``topic`` is the ASCII topic string and ``payload`` is ``contracts.dumps(event)``
(JSON, at most ``MAX_WIRE_BYTES``). The broker adds an envelope frame in the middle of what it sends to
subscribers, so a subscriber reads the FIRST frame (topic) and the LAST frame (payload). Only
``contracts.WIRE_TOPICS`` are ever sent or accepted.

Sockets
    * ``SUB`` connects to the broker's XPUB endpoint (``BUS_SUB_ENDPOINT``) and receives what chat subscribed to.
    * ``XPUB`` connects to the broker's XSUB endpoint (``BUS_PUB_ENDPOINT``) and PUBLISHES. It is an XPUB, not a
      PUB, on purpose: the subscription messages that the broker's XSUB sends upstream tell us when somebody is
      really listening on a topic. A PUB socket silently drops what it sends before that handshake (slow
      joiner) and after a broker restart; here an event is only released to the socket when its topic is
      subscribed, and ``XPUB_NODROP`` turns a full send queue into back-pressure instead of a silent drop.

Delivery policy (publish never blocks and never raises for a slow or missing broker)
    * ``Topic.HOLD`` (``NEVER_DROP_TOPICS``) goes to a separate queue that is NEVER trimmed, never expires, and
      is only released when the broker is subscribed to ``control.hold``. A hold published while the broker is
      down is delivered after it comes back. It is at-most-once beyond that point (ZeroMQ has no acks): a hold
      sitting inside a socket buffer at the instant of a crash can be lost, which is a limit of PUB/SUB.
    * Every other topic goes to one bounded queue (``queue_max``): the oldest entry is dropped and counted when
      it overflows, and an entry older than ``stale_after_s`` is discarded (stale chat is useless).
    * Inbound events fan out to bounded drop-oldest subscriptions; ``Topic.HOLD`` is never dropped there either.

One I/O thread owns both sockets (ZeroMQ sockets are not thread-safe), so ``publish`` may be called from any
thread, including the event loop. Reconnects are ZeroMQ's own (``RECONNECT_IVL``); ``on_state`` is told when
the broker's subscription appears or disappears. Nothing here logs an event payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
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

_LOG = logging.getLogger("chat.zmq_bus")

DEFAULT_SUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus"  # broker XPUB: we subscribe here
DEFAULT_PUB_ENDPOINT = "ipc:///tmp/streamsarthi-bus-in"  # broker XSUB: we publish here
_POLL_MS = 100


class BusError(RuntimeError):
    """The bus could not start (pyzmq missing, endpoint syntax invalid)."""


def _require_wire_topic(topic: Topic) -> Topic:
    topic = Topic(topic)
    if topic not in WIRE_TOPICS:
        raise ValueError(f"topic {topic.value} is local to the engine and is never on the wire")
    return topic


class ZmqSubscription:
    """Thread-safe bounded ``(topic, event)`` queue consumed with ``async for``. HOLD is never dropped."""

    def __init__(self, bus: ZmqBus, topics: frozenset[Topic], maxsize: int) -> None:
        self._bus = bus
        self.topics = topics
        self.maxsize = maxsize
        self.dropped = 0
        self._dq: deque[tuple[Topic, Any]] = deque()
        self._lock = threading.Lock()
        self._waiter: tuple[asyncio.AbstractEventLoop, asyncio.Future[None]] | None = None
        self._closed = False

    def _push(self, topic: Topic, event: Any) -> None:
        with self._lock:
            if self._closed:
                return
            if len(self._dq) >= self.maxsize:
                for i, (queued, _) in enumerate(self._dq):
                    if queued not in NEVER_DROP_TOPICS:
                        del self._dq[i]
                        self.dropped += 1
                        break
            self._dq.append((topic, event))
            waiter, self._waiter = self._waiter, None
        _wake(waiter)

    def __len__(self) -> int:
        return len(self._dq)

    def empty(self) -> bool:
        return not self._dq

    def get_nowait(self) -> tuple[Topic, Any]:
        with self._lock:
            if not self._dq:
                raise asyncio.QueueEmpty
            return self._dq.popleft()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            waiter, self._waiter = self._waiter, None
        self._bus._remove(self)
        _wake(waiter)

    def __aiter__(self) -> ZmqSubscription:
        return self

    async def __anext__(self) -> tuple[Topic, Any]:
        loop = asyncio.get_running_loop()
        while True:
            with self._lock:
                if self._dq:
                    return self._dq.popleft()
                if self._closed:
                    raise StopAsyncIteration
                fut: asyncio.Future[None] = loop.create_future()
                self._waiter = (loop, fut)
            try:
                await fut
            finally:
                with self._lock:
                    if self._waiter is not None and self._waiter[1] is fut:
                        self._waiter = None

    async def __aenter__(self) -> ZmqSubscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


def _wake(waiter: tuple[asyncio.AbstractEventLoop, asyncio.Future[None]] | None) -> None:
    if waiter is None:
        return
    loop, fut = waiter

    def resolve() -> None:
        if not fut.done():
            fut.set_result(None)

    with contextlib.suppress(RuntimeError):  # the loop is already closed
        loop.call_soon_threadsafe(resolve)


class ZmqBus:
    """Implements ``contracts.EventBus`` over ZeroMQ. ``start()`` before use, ``stop()`` when done."""

    def __init__(
        self,
        sub_endpoint: str = DEFAULT_SUB_ENDPOINT,
        pub_endpoint: str = DEFAULT_PUB_ENDPOINT,
        *,
        queue_max: int = 2048,
        subscription_max: int = 1024,
        stale_after_s: float = 30.0,
        on_state: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if queue_max < 1 or subscription_max < 1:
            raise ValueError("queue sizes must be positive")
        self.sub_endpoint = sub_endpoint
        self.pub_endpoint = pub_endpoint
        self._queue_max = queue_max
        self._sub_max = subscription_max
        self._stale_after_s = stale_after_s
        self._on_state = on_state
        self._clock = clock

        self._lock = threading.Lock()
        self._holds: deque[tuple[bytes, bytes]] = deque()  # never trimmed
        self._out: deque[tuple[bytes, bytes, float]] = deque()  # bounded, droppable
        self._subs: tuple[ZmqSubscription, ...] = ()
        self._wanted: set[Topic] = set()  # topics to subscribe the SUB socket to
        self._applied: set[Topic] = set()  # I/O thread only
        self._peer: set[bytes] = set()  # prefixes the broker subscribed to on our XPUB (I/O thread writes)
        self._connected = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake_r: socket.socket | None = None
        self._wake_w: socket.socket | None = None
        self.published = 0
        self.sent = 0
        self.received = 0
        self.dropped_out = 0
        self.expired_out = 0
        self.rejected_out = 0
        self.rejected_in = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            import zmq  # noqa: F401
        except ImportError as exc:
            raise BusError("pyzmq is not installed") from exc
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._stop.clear()
        started = threading.Event()
        errors: list[BaseException] = []
        self._thread = threading.Thread(
            target=self._run, args=(started, errors), name="chat-zmq-bus", daemon=True
        )
        self._thread.start()
        started.wait(5.0)
        if errors:
            self._thread.join(timeout=2.0)
            self._thread = None
            raise BusError("bus sockets could not be opened") from errors[0]

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        self._poke()
        thread.join(timeout=3.0)
        self._thread = None
        for sub in self._subs:
            sub.close()
        for sock in (self._wake_r, self._wake_w):
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()
        self._wake_r = self._wake_w = None

    @property
    def connected(self) -> bool:
        """The broker is subscribed to something we publish (its XSUB is reachable)."""
        return self._connected

    @property
    def pending(self) -> tuple[int, int]:
        """``(holds waiting, other events waiting)`` for the broker."""
        with self._lock:
            return len(self._holds), len(self._out)

    @property
    def stats(self) -> dict[str, int]:
        holds, other = self.pending
        return {
            "published": self.published,
            "sent": self.sent,
            "received": self.received,
            "dropped_out": self.dropped_out,
            "expired_out": self.expired_out,
            "rejected_out": self.rejected_out,
            "rejected_in": self.rejected_in,
            "pending_holds": holds,
            "pending_other": other,
        }

    # ------------------------------------------------------------------ EventBus
    def publish(self, topic: Topic, event: Any) -> None:
        """Never blocks. ``ValueError`` for a local-only topic, ``TypeError`` for the wrong payload type."""
        topic = _require_wire_topic(topic)
        expected = TOPIC_TYPES[topic]
        if not isinstance(event, expected):
            raise TypeError(f"topic {topic.value} carries {expected.__name__}, got {type(event).__name__}")
        try:
            payload = dumps(event)
        except ContractError:
            self.rejected_out += 1  # oversized or invalid: never retried, never logged with content
            _LOG.warning("bus publish rejected topic=%s code=wire_invalid", topic.value)
            return
        name = topic.value.encode("ascii")
        with self._lock:
            self.published += 1
            if topic in NEVER_DROP_TOPICS:
                self._holds.append((name, payload))
            else:
                self._out.append((name, payload, self._clock()))
                while len(self._out) > self._queue_max:
                    self._out.popleft()
                    self.dropped_out += 1
        self._poke()

    def subscribe(self, topics: Sequence[Topic]) -> ZmqSubscription:
        wanted = frozenset(_require_wire_topic(t) for t in topics)
        sub = ZmqSubscription(self, wanted, self._sub_max)
        with self._lock:
            self._subs = (*self._subs, sub)
            self._wanted |= wanted
        self._poke()
        return sub

    def _remove(self, sub: ZmqSubscription) -> None:
        with self._lock:
            self._subs = tuple(s for s in self._subs if s is not sub)

    # ------------------------------------------------------------------ I/O thread
    def _poke(self) -> None:
        sock = self._wake_w
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.send(b"\x00")

    def _set_connected(self, value: bool) -> None:
        if value == self._connected:
            return
        self._connected = value
        if self._on_state is not None:
            try:
                self._on_state("bus_connected" if value else "bus_disconnected")
            except Exception as exc:  # a status sink must never break the bus
                _LOG.warning("bus state callback failed code=%s", type(exc).__name__)

    def _run(self, started: threading.Event, errors: list[BaseException]) -> None:
        import zmq

        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        pub = ctx.socket(zmq.XPUB)
        try:
            for sock in (sub, pub):
                sock.setsockopt(zmq.LINGER, 0)
                sock.setsockopt(zmq.RECONNECT_IVL, 200)
                sock.setsockopt(zmq.RECONNECT_IVL_MAX, 2000)
            sub.setsockopt(zmq.RCVHWM, 5000)
            pub.setsockopt(zmq.SNDHWM, 5000)
            pub.setsockopt(zmq.XPUB_VERBOSE, 1)  # see every (re)subscription
            pub.setsockopt(zmq.XPUB_NODROP, 1)  # a full send queue raises zmq.Again instead of dropping
            # XPUB never reports the unsubscriptions of a peer that vanished, so loss of the connection is
            # read from the socket monitor; the broker's XSUB re-sends its subscriptions after a reconnect.
            monitor = pub.get_monitor_socket(zmq.EVENT_DISCONNECTED | zmq.EVENT_CLOSED)
            sub.connect(self.sub_endpoint)
            pub.connect(self.pub_endpoint)
        except Exception as exc:
            errors.append(exc)
            started.set()
            sub.close(0)
            pub.close(0)
            ctx.term()
            return
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        poller.register(pub, zmq.POLLIN)
        poller.register(monitor, zmq.POLLIN)
        assert self._wake_r is not None
        wake_fd = self._wake_r.fileno()
        poller.register(wake_fd, zmq.POLLIN)
        started.set()
        try:
            while not self._stop.is_set():
                events = dict(poller.poll(_POLL_MS))
                if wake_fd in events:
                    self._drain_wake()
                self._apply_subscriptions(sub)
                if monitor in events:
                    self._read_monitor(monitor)
                if pub in events:
                    self._read_peer_subscriptions(pub)
                if sub in events:
                    self._read_inbound(sub)
                self._flush(pub)
        except Exception as exc:  # the chat process must survive a broken bus
            _LOG.error("bus thread stopped code=%s", type(exc).__name__)
        finally:
            self._set_connected(False)
            with contextlib.suppress(Exception):
                pub.disable_monitor()
                monitor.close(0)
            sub.close(0)
            pub.close(0)
            ctx.term()

    def _read_monitor(self, monitor: Any) -> None:
        import zmq
        from zmq.utils.monitor import recv_monitor_message

        for _ in range(64):
            try:
                event = recv_monitor_message(monitor, zmq.NOBLOCK)
            except zmq.Again:
                break
            if event["event"] in (zmq.EVENT_DISCONNECTED, zmq.EVENT_CLOSED):
                self._peer.clear()  # nothing is released until the broker subscribes again
                self._set_connected(False)

    def _drain_wake(self) -> None:
        sock = self._wake_r
        if sock is None:
            return
        with contextlib.suppress(BlockingIOError, OSError):
            while sock.recv(4096):
                pass

    def _apply_subscriptions(self, sub: Any) -> None:
        import zmq

        with self._lock:
            new = self._wanted - self._applied
        for topic in new:
            sub.setsockopt(zmq.SUBSCRIBE, topic.value.encode("ascii"))
            self._applied.add(topic)

    def _read_peer_subscriptions(self, pub: Any) -> None:
        import zmq

        for _ in range(256):
            try:
                message = pub.recv(zmq.NOBLOCK)
            except zmq.Again:
                break
            if not message:
                continue
            prefix = message[1:]
            if message[0] == 1:
                self._peer.add(prefix)
            elif message[0] == 0:
                self._peer.discard(prefix)
        self._set_connected(bool(self._peer))

    def _ready(self, name: bytes) -> bool:
        return any(name.startswith(prefix) for prefix in self._peer)

    def _flush(self, pub: Any) -> None:
        """Release what the broker is subscribed to. Runs under the lock: every send is NOBLOCK and fast."""
        import zmq

        with self._lock:
            if not self._peer:
                self._expire_locked()
                return
            while self._holds:
                name, payload = self._holds[0]
                if not self._ready(name):
                    break  # hold order is kept: nothing behind a blocked hold is released
                try:
                    pub.send_multipart([name, payload], zmq.NOBLOCK)
                except zmq.Again:
                    break
                self._holds.popleft()
                self.sent += 1
            now = self._clock()
            kept: deque[tuple[bytes, bytes, float]] = deque()
            blocked = False
            for item in self._out:
                name, payload, at = item
                if now - at > self._stale_after_s:
                    self.expired_out += 1
                    continue
                if blocked or not self._ready(name):
                    kept.append(item)
                    continue
                try:
                    pub.send_multipart([name, payload], zmq.NOBLOCK)
                except zmq.Again:
                    blocked = True
                    kept.append(item)
                    continue
                self.sent += 1
            self._out = kept

    def _expire_locked(self) -> None:
        now = self._clock()
        while self._out and now - self._out[0][2] > self._stale_after_s:
            self._out.popleft()
            self.expired_out += 1

    def _read_inbound(self, sub: Any) -> None:
        import zmq

        for _ in range(256):
            try:
                frames = sub.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                break
            self._deliver(frames)

    def _deliver(self, frames: list[bytes]) -> None:
        if len(frames) < 2 or len(frames[-1]) > MAX_WIRE_BYTES:
            self.rejected_in += 1
            return
        try:
            topic = Topic(frames[0].decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            self.rejected_in += 1
            return
        if topic not in WIRE_TOPICS:
            self.rejected_in += 1
            return
        try:
            event = loads(frames[-1])
        except ContractError:
            self.rejected_in += 1
            return
        if not isinstance(event, TOPIC_TYPES[topic]):
            self.rejected_in += 1
            return
        self.received += 1
        for sub in self._subs:
            if topic in sub.topics:
                sub._push(topic, event)


def endpoints_from_env(env: dict[str, str] | None = None) -> tuple[str, str]:
    """``(sub_endpoint, pub_endpoint)`` from ``BUS_SUB_ENDPOINT`` / ``BUS_PUB_ENDPOINT`` (the names the API uses)."""
    import os

    source = os.environ if env is None else env
    return (
        source.get("BUS_SUB_ENDPOINT", DEFAULT_SUB_ENDPOINT),
        source.get("BUS_PUB_ENDPOINT", DEFAULT_PUB_ENDPOINT),
    )


__all__ = [
    "DEFAULT_PUB_ENDPOINT",
    "DEFAULT_SUB_ENDPOINT",
    "BusError",
    "ZmqBus",
    "ZmqSubscription",
    "endpoints_from_env",
]
