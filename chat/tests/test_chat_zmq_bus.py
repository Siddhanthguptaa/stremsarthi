"""ZmqBus against a tiny in-test XSUB/XPUB broker (same shape as the engine's): publish, subscribe, reconnect,
bounded queues, hold never dropped. CPU only, loopback tcp."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest

zmq = pytest.importorskip("zmq")

from chat.zmq_bus import ZmqBus  # noqa: E402
from contracts import (  # noqa: E402
    ChatAction,
    ChatMessage,
    ChatVerdict,
    ConfigChange,
    EventBus,
    HoldCommand,
    HoldSource,
    ModAction,
    Topic,
    dumps,
    loads,
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class TinyBroker:
    """XSUB (bind) <-> XPUB (bind) proxy that records every message publishers send."""

    def __init__(self, xpub_port: int, xsub_port: int, upstream: tuple[Topic, ...] = ()) -> None:
        self.xpub_ep = f"tcp://127.0.0.1:{xpub_port}"
        self.xsub_ep = f"tcp://127.0.0.1:{xsub_port}"
        self.upstream = upstream
        self.seen: list[tuple[str, bytes]] = []
        self._inject: list[list[bytes]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> TinyBroker:
        self._thread.start()
        assert self._ready.wait(5)
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(3)

    def inject(self, topic: Topic, event: object, *, raw: bytes | None = None) -> None:
        payload = raw if raw is not None else dumps(event)
        with self._lock:
            self._inject.append([topic.value.encode(), b"1:1", payload])  # envelope frame like the engine

    def events(self, topic: Topic) -> list[object]:
        return [loads(p) for t, p in list(self.seen) if t == topic.value]

    def _run(self) -> None:
        ctx = zmq.Context()
        xsub, xpub = ctx.socket(zmq.XSUB), ctx.socket(zmq.XPUB)
        for s in (xsub, xpub):
            s.setsockopt(zmq.LINGER, 0)
        xsub.bind(self.xsub_ep)
        xpub.bind(self.xpub_ep)
        for topic in self.upstream:  # the engine subscribes control topics itself
            xsub.send(b"\x01" + topic.value.encode())
        poller = zmq.Poller()
        poller.register(xsub, zmq.POLLIN)
        poller.register(xpub, zmq.POLLIN)
        self._ready.set()
        while not self._stop.is_set():
            events = dict(poller.poll(10))
            if xsub in events:
                while True:
                    try:
                        frames = xsub.recv_multipart(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    self.seen.append((frames[0].decode(), frames[-1]))
                    xpub.send_multipart(frames)
            if xpub in events:
                while True:
                    try:
                        message = xpub.recv(zmq.NOBLOCK)
                    except zmq.Again:
                        break
                    xsub.send(message)  # propagate (un)subscriptions upstream
            with self._lock:
                pending, self._inject = self._inject, []
            for frames in pending:
                xpub.send_multipart(frames)
        xsub.close(0)
        xpub.close(0)
        ctx.term()


@pytest.fixture
def ports() -> tuple[int, int]:
    return free_port(), free_port()


@pytest.fixture
def bus_factory(ports: tuple[int, int]) -> Iterator[Callable[..., ZmqBus]]:
    made: list[ZmqBus] = []

    def make(**kwargs: object) -> ZmqBus:
        bus = ZmqBus(f"tcp://127.0.0.1:{ports[0]}", f"tcp://127.0.0.1:{ports[1]}", **kwargs)  # type: ignore[arg-type]
        bus.start()
        made.append(bus)
        return bus

    yield make
    for bus in made:
        bus.stop()


def message(i: int = 1) -> ChatMessage:
    return ChatMessage(
        platform="twitch",
        message_id=f"m{i}",
        channel_id="c1",
        author_id="a1",
        author_display="Asha",
        text="namaste",
        received_at=1.0,
    )


def verdict(i: int = 1) -> ChatVerdict:
    return ChatVerdict(
        platform="twitch",
        message_id=f"m{i}",
        action=ChatAction.ALLOW,
        categories=(),
        confidence=0.9,
        reason="clean",
        stage="rules",
        latency_ms=1.0,
    )


def test_is_an_event_bus(bus_factory: Callable[..., ZmqBus]) -> None:
    assert isinstance(bus_factory(), EventBus)


def test_publish_reaches_the_broker_in_wire_format(
    ports: tuple[int, int], bus_factory: Callable[..., ZmqBus]
) -> None:
    broker = TinyBroker(ports[0], ports[1], upstream=(Topic.CHAT_VERDICT, Topic.CHAT_ALLOWED)).start()
    try:
        bus = bus_factory()
        assert wait_for(lambda: bus.connected)
        bus.publish(Topic.CHAT_VERDICT, verdict())
        bus.publish(Topic.CHAT_ALLOWED, message())
        assert wait_for(lambda: len(broker.seen) == 2)
        assert broker.events(Topic.CHAT_VERDICT) == [verdict()]
        assert broker.events(Topic.CHAT_ALLOWED) == [message()]
    finally:
        broker.stop()


def test_publish_validates_topic_and_type(bus_factory: Callable[..., ZmqBus]) -> None:
    bus = bus_factory()
    with pytest.raises(ValueError):
        bus.publish(Topic.TRANSCRIPT, message())  # local-only topic never leaves the process
    with pytest.raises(TypeError):
        bus.publish(Topic.CHAT_VERDICT, message())
    with pytest.raises(ValueError):
        bus.subscribe([Topic.DETECTION])


def test_hold_survives_flood_and_broker_start_after_publish(
    ports: tuple[int, int], bus_factory: Callable[..., ZmqBus]
) -> None:
    bus = bus_factory(queue_max=50, stale_after_s=600.0)
    hold = HoldCommand(source=HoldSource.CHAT, active=True, actor="abc")
    for i in range(10):
        bus.publish(Topic.CHAT_VERDICT, verdict(i))
    bus.publish(Topic.HOLD, hold)
    for i in range(10, 400):
        bus.publish(Topic.CHAT_VERDICT, verdict(i))  # far beyond the bound: oldest droppable go
    holds, other = bus.pending
    assert holds == 1 and other == 50
    assert bus.stats["dropped_out"] == 350
    broker = TinyBroker(ports[0], ports[1], upstream=(Topic.HOLD, Topic.CHAT_VERDICT)).start()
    try:
        assert wait_for(lambda: bool(broker.events(Topic.HOLD)))
        assert broker.events(Topic.HOLD) == [hold]
        assert wait_for(lambda: len(broker.events(Topic.CHAT_VERDICT)) == 50)
        ids = [v.message_id for v in broker.events(Topic.CHAT_VERDICT)]  # type: ignore[attr-defined]
        assert ids == [f"m{i}" for i in range(350, 400)]  # newest kept, in order
        assert bus.pending == (0, 0)
    finally:
        broker.stop()


def test_stale_droppable_events_expire_but_holds_do_not(bus_factory: Callable[..., ZmqBus]) -> None:
    now = [0.0]
    bus = bus_factory(stale_after_s=5.0, clock=lambda: now[0])
    bus.publish(Topic.CHAT_VERDICT, verdict())
    bus.publish(Topic.HOLD, HoldCommand(source=HoldSource.CHAT, active=True, actor="abc"))
    now[0] = 100.0
    assert wait_for(lambda: bus.stats["expired_out"] == 1)
    assert bus.pending == (1, 0)


def test_unsubscribed_topics_wait_instead_of_being_lost(
    ports: tuple[int, int], bus_factory: Callable[..., ZmqBus]
) -> None:
    broker = TinyBroker(ports[0], ports[1], upstream=(Topic.HOLD,)).start()
    try:
        bus = bus_factory(stale_after_s=600.0)
        assert wait_for(lambda: bus.connected)
        bus.publish(Topic.CHAT_VERDICT, verdict())  # nobody subscribed to chat.verdict yet
        time.sleep(0.2)
        assert broker.seen == [] and bus.pending == (0, 1)
        broker.stop()
    finally:
        broker.stop()


def test_subscribe_receives_decoded_events_and_rejects_bad_frames(
    ports: tuple[int, int], bus_factory: Callable[..., ZmqBus]
) -> None:
    broker = TinyBroker(ports[0], ports[1]).start()
    try:
        bus = bus_factory()
        sub = bus.subscribe([Topic.CONFIG, Topic.CHAT_MODACTION])
        change = ConfigChange(diff={"chat.enabled": True}, actor="api")
        action = ModAction(
            message_id="m1", platform="twitch", action="approve", timeout_seconds=None, actor="mod1"
        )

        async def collect() -> list[tuple[Topic, object]]:
            got: list[tuple[Topic, object]] = []
            async for item in sub:
                got.append(item)
                if len(got) == 2:
                    break
            return got

        async def go() -> list[tuple[Topic, object]]:
            task = asyncio.ensure_future(collect())
            for _ in range(100):  # the SUB filter reaches the broker asynchronously: keep injecting
                broker.inject(Topic.CONFIG, None, raw=b"{not json")  # bad JSON
                broker.inject(Topic.CHAT_MODACTION, change)  # right wire type, wrong topic payload
                broker.inject(Topic.CONFIG, change)
                broker.inject(Topic.CHAT_MODACTION, action)
                await asyncio.sleep(0.05)
                if task.done():
                    break
            return await asyncio.wait_for(task, 5)

        got = asyncio.run(go())
        assert [t for t, _ in got] == [Topic.CONFIG, Topic.CHAT_MODACTION]
        assert got[0][1] == change and got[1][1] == action
        assert bus.stats["rejected_in"] >= 2
    finally:
        broker.stop()


def test_reconnects_after_broker_restart(ports: tuple[int, int], bus_factory: Callable[..., ZmqBus]) -> None:
    states: list[str] = []
    upstream = (Topic.HOLD, Topic.CHAT_VERDICT)
    broker = TinyBroker(ports[0], ports[1], upstream=upstream).start()
    bus = bus_factory(on_state=states.append)
    assert wait_for(lambda: bus.connected)
    bus.publish(Topic.CHAT_VERDICT, verdict(1))
    assert wait_for(lambda: len(broker.events(Topic.CHAT_VERDICT)) == 1)
    broker.stop()
    assert wait_for(lambda: not bus.connected, 8.0)
    hold = HoldCommand(source=HoldSource.CHAT, active=True, actor="abc")
    bus.publish(Topic.HOLD, hold)  # broker is down: must wait, not vanish
    assert bus.pending[0] == 1
    broker2 = TinyBroker(ports[0], ports[1], upstream=upstream).start()
    try:
        assert wait_for(lambda: bus.connected, 10.0)
        assert wait_for(lambda: broker2.events(Topic.HOLD) == [hold], 10.0)
        bus.publish(Topic.CHAT_VERDICT, verdict(2))
        assert wait_for(lambda: len(broker2.events(Topic.CHAT_VERDICT)) == 1)
    finally:
        broker2.stop()
    assert states[0] == "bus_connected" and "bus_disconnected" in states


def test_hold_from_chat_pipeline_travels_over_zmq(
    ports: tuple[int, int], bus_factory: Callable[..., ZmqBus]
) -> None:
    from chat.pipeline import ChatPipeline

    broker = TinyBroker(ports[0], ports[1], upstream=(Topic.HOLD,)).start()
    try:
        bus = bus_factory()

        async def go() -> None:
            pipeline = ChatPipeline(bus)
            pipeline.submit(
                ChatMessage(
                    platform="twitch",
                    message_id="h1",
                    channel_id="c1",
                    author_id="mod1",
                    author_display="Mod",
                    text="!hold",
                    received_at=1.0,
                    is_moderator=True,
                )
            )
            await pipeline.stop()

        asyncio.run(go())
        assert wait_for(lambda: len(broker.events(Topic.HOLD)) == 1)
        (hold,) = broker.events(Topic.HOLD)
        assert hold.active is True and hold.source == HoldSource.CHAT  # type: ignore[attr-defined]
    finally:
        broker.stop()
