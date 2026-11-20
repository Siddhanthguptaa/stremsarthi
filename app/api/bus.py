"""Bus client abstraction for the API process.

``BusClient`` is what the rest of the API sees. Two implementations:

* ``ZmqBusClient``: pyzmq. A SUB socket connects to the engine broker's XPUB endpoint and a PUB
  socket connects to its XSUB endpoint (``engine/bus.py`` runs the XSUB/XPUB proxy). Frames are
  ``[topic bytes, contracts.wire.dumps(event)]``; only ``WIRE_TOPICS`` are ever read or written.
* ``InProcessBusClient``: wraps ``contracts.fakes.InProcessBus`` for tests and single-process runs.

The client imports only ``contracts``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from contracts import TOPIC_TYPES, WIRE_TOPICS, ContractError, Topic, dumps, loads
from contracts.fakes import InProcessBus

log = logging.getLogger("streamsarthi.api.bus")

# What the API reads from the bus (spec section 3, plus the aggregated risk meter).
API_TOPICS: tuple[Topic, ...] = (
    Topic.SAFETY_EVENT,
    Topic.RISK,
    Topic.RISK_METER,
    Topic.CAPTION,
    Topic.CHAT_ALLOWED,
    Topic.CHAT_VERDICT,
    Topic.CHAT_MODQUEUE,
    Topic.HOLD,
    Topic.ENGINE_STATUS,
)


@runtime_checkable
class Subscription(Protocol):
    """Async iterator of ``(topic, event)`` with a ``close()``."""

    def __aiter__(self) -> Subscription: ...
    async def __anext__(self) -> tuple[Topic, Any]: ...
    def close(self) -> None: ...


@runtime_checkable
class BusClient(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def publish(self, topic: Topic, event: Any) -> None:
        """Non-blocking. Raises ``ValueError`` for a topic that is not bridged, ``TypeError`` for
        a payload of the wrong type."""
        ...

    def subscribe(self, topics: Sequence[Topic]) -> Subscription: ...
    @property
    def connected(self) -> bool:
        """The broker (engine) endpoint is reachable."""
        ...


def _require_wire_topic(topic: Topic) -> None:
    if topic not in WIRE_TOPICS:
        raise ValueError(f"topic {topic.value} is local to the engine and is never on the wire")


class InProcessBusClient:
    """``BusClient`` over ``InProcessBus``. ``connected`` is always true."""

    def __init__(self, bus: InProcessBus | None = None) -> None:
        self.bus = bus if bus is not None else InProcessBus()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @property
    def connected(self) -> bool:
        return True

    def publish(self, topic: Topic, event: Any) -> None:
        _require_wire_topic(topic)
        self.bus.publish(topic, event)

    def subscribe(self, topics: Sequence[Topic]) -> Subscription:
        for t in topics:
            _require_wire_topic(t)
        return self.bus.subscribe(topics)


class _FanOut(InProcessBus):
    """Local fan-out of decoded wire events: bounded drop-oldest queues, ``control.hold`` never
    dropped. The history list the fake keeps for tests is cleared so memory stays flat."""

    def publish(self, topic: Topic, event: Any) -> None:
        super().publish(topic, event)
        self.history.clear()


class ZmqBusClient:
    def __init__(
        self,
        sub_endpoint: str,
        pub_endpoint: str,
        topics: Sequence[Topic] = API_TOPICS,
        queue_max: int = 1024,
    ) -> None:
        for t in topics:
            _require_wire_topic(t)
        self.sub_endpoint = sub_endpoint
        self.pub_endpoint = pub_endpoint
        self._topics = tuple(topics)
        self._fanout = _FanOut(maxsize=queue_max)
        self._task: asyncio.Task[None] | None = None
        self._pub: Any = None
        self._sub: Any = None
        self._ctx: Any = None
        self.last_rx: float | None = None  # time.monotonic() of the last decoded event
        self.rejected = 0  # frames dropped for failing the contract (bad type, size, topic)
        self.publish_dropped = 0

    # --- lifecycle -------------------------------------------------------------------------
    async def start(self) -> None:
        import zmq
        import zmq.asyncio

        if self._task is not None:
            return
        self._ctx = zmq.asyncio.Context()
        self._sub = self._ctx.socket(zmq.SUB)
        self._sub.setsockopt(zmq.LINGER, 0)
        self._sub.setsockopt(zmq.RCVHWM, 5000)
        self._sub.setsockopt(zmq.RECONNECT_IVL, 200)
        self._sub.setsockopt(zmq.RECONNECT_IVL_MAX, 2000)
        for t in self._topics:
            self._sub.setsockopt(zmq.SUBSCRIBE, t.value.encode())
        self._sub.connect(self.sub_endpoint)
        self._pub = self._ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 200)
        self._pub.setsockopt(zmq.SNDHWM, 5000)
        self._pub.setsockopt(zmq.RECONNECT_IVL, 200)
        self._pub.setsockopt(zmq.RECONNECT_IVL_MAX, 2000)
        self._pub.connect(self.pub_endpoint)
        self._task = asyncio.create_task(self._recv_loop(), name="bus-recv")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for sock in (self._sub, self._pub):
            if sock is not None:
                sock.close(linger=0)
        self._sub = self._pub = None
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

    @property
    def connected(self) -> bool:
        """True when the broker's ipc socket exists (or, for tcp, once started)."""
        if self._task is None:
            return False
        for endpoint in (self.sub_endpoint, self.pub_endpoint):
            if endpoint.startswith("ipc://") and not os.path.exists(endpoint[len("ipc://") :]):
                return False
        return True

    # --- data ------------------------------------------------------------------------------
    def publish(self, topic: Topic, event: Any) -> None:
        import zmq

        _require_wire_topic(topic)
        expected = TOPIC_TYPES[topic]
        if not isinstance(event, expected):
            raise TypeError(f"topic {topic.value} carries {expected.__name__}, got {type(event).__name__}")
        if self._pub is None:
            raise RuntimeError("bus client is not started")
        payload = dumps(event)
        try:
            self._pub.send_multipart([topic.value.encode(), payload], flags=zmq.NOBLOCK, copy=False)
        except zmq.Again:
            self.publish_dropped += 1
            log.warning("bus publish dropped on %s (send queue full)", topic.value)

    def subscribe(self, topics: Sequence[Topic]) -> Subscription:
        for t in topics:
            _require_wire_topic(t)
            if t not in self._topics:
                raise ValueError(f"topic {t.value} is not subscribed by this client")
        return self._fanout.subscribe(topics)

    async def _recv_loop(self) -> None:
        while True:
            frames = await self._sub.recv_multipart()
            try:
                self._deliver(frames)
            except Exception:  # never let one bad frame stop the loop
                self.rejected += 1
                log.warning("bus frame rejected")

    def _deliver(self, frames: list[Any]) -> None:
        # [topic, payload]; the broker may add envelope frames between them (seq), ignored here.
        if len(frames) < 2:
            self.rejected += 1
            return
        raw_topic = bytes(frames[0])
        try:
            topic = Topic(raw_topic.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            self.rejected += 1
            return
        if topic not in WIRE_TOPICS or topic not in self._topics:
            self.rejected += 1
            return
        try:
            event = loads(bytes(frames[-1]))
        except ContractError:
            self.rejected += 1
            return
        if not isinstance(event, TOPIC_TYPES[topic]):
            self.rejected += 1
            return
        self.last_rx = time.monotonic()
        self._fanout.publish(topic, event)


def make_bus(backend: str, sub_endpoint: str, pub_endpoint: str) -> BusClient:
    if backend == "inprocess":
        return InProcessBusClient()
    return ZmqBusClient(sub_endpoint, pub_endpoint)
