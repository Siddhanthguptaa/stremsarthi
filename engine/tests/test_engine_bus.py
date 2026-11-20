"""``LocalBus`` and the ZeroMQ broker over real ``ipc://`` sockets (a unique path per test)."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
import threading
import time

import pytest
import zmq

from contracts import (
    TOPIC_TYPES,
    WIRE_TOPICS,
    Category,
    ConfigChange,
    HoldCommand,
    HoldSource,
    Modality,
    MuteSpan,
    SafetyEvent,
    Topic,
    dumps,
    loads,
)
from contracts.enums import LOCAL_ONLY_TOPICS
from engine.bus import BrokerError, LocalBus, ZmqBroker


def hold(active=True, source=HoldSource.HOTKEY) -> HoldCommand:
    return HoldCommand(source, active, "tester")


def safety(n: int = 0) -> SafetyEvent:
    return SafetyEvent(
        event_id=f"e{n}",
        session_id="s1",
        wall_ts="2026-11-20T00:00:00+00:00",
        pts=1.0,
        modality=Modality.VIDEO,
        category=Category.AADHAAR,
        action="masked",
        confidence=0.9,
        bbox=None,
        duration_ms=None,
        source="fake",
        detail_code="verhoeff_ok",
    )


def mute() -> MuteSpan:
    return MuteSpan("m1", 1.0, 2.0, Category.SPOKEN_PHONE, "segment", 0.9, "digits", 0.0)


# ------------------------------------------------------------------ LocalBus


def test_payload_type_is_checked_per_topic():
    bus = LocalBus()
    with pytest.raises(TypeError):
        bus.publish(Topic.HOLD, safety())
    with pytest.raises(TypeError):
        bus.publish(Topic.SAFETY_EVENT, "text")
    bus.publish(Topic.HOLD, hold())


def test_every_topic_has_a_type_and_the_bus_accepts_it():
    assert set(TOPIC_TYPES) == set(Topic)
    assert LOCAL_ONLY_TOPICS.isdisjoint(WIRE_TOPICS)


def test_drop_oldest_with_counter():
    bus = LocalBus()
    sub = bus.subscribe([Topic.SAFETY_EVENT], maxsize=3)
    for n in range(5):
        bus.publish(Topic.SAFETY_EVENT, safety(n))
    assert sub.dropped == 2 and bus.stats["dropped"] == 2
    assert [e.event_id for _, e in sub.drain()] == ["e2", "e3", "e4"]  # the oldest two were dropped


def test_control_hold_is_never_dropped():
    bus = LocalBus()
    sub = bus.subscribe([Topic.HOLD, Topic.SAFETY_EVENT], maxsize=2)
    bus.publish(Topic.HOLD, hold(True, HoldSource.HOTKEY))
    bus.publish(Topic.HOLD, hold(True, HoldSource.CHAT))
    for n in range(10):  # a flood of telemetry on a full queue
        bus.publish(Topic.SAFETY_EVENT, safety(n))
    bus.publish(Topic.HOLD, hold(False, HoldSource.HOTKEY))
    items = sub.drain()
    holds = [e for t, e in items if t is Topic.HOLD]
    assert [(h.source, h.active) for h in holds] == [
        (HoldSource.HOTKEY, True),
        (HoldSource.CHAT, True),
        (HoldSource.HOTKEY, False),
    ]
    assert sub.dropped > 0


def test_handlers_run_in_the_publisher_and_a_failing_handler_is_isolated():
    bus = LocalBus()
    seen: list[str] = []
    bus.on(Topic.HOLD, lambda e: seen.append(threading.current_thread().name))
    bus.on(Topic.HOLD, lambda e: 1 / 0)
    bus.on(Topic.HOLD, lambda e: seen.append("after"))
    bus.publish(Topic.HOLD, hold())  # must not raise
    assert seen == [threading.current_thread().name, "after"] and bus.handler_errors == 1


def test_unsubscribe_and_close():
    bus = LocalBus()
    seen: list[object] = []
    off = bus.on(Topic.HOLD, seen.append)
    sub = bus.subscribe([Topic.HOLD])
    bus.publish(Topic.HOLD, hold())
    off()
    sub.close()
    bus.publish(Topic.HOLD, hold(False))
    assert len(seen) == 1 and bus.stats["subscribers"] == 0
    assert sub.get(0.01) is not None  # the queued event is still readable
    assert sub.get(0.01) is None


def test_thread_safe_publishing():
    bus = LocalBus(maxsize=100_000)
    sub = bus.subscribe([Topic.SAFETY_EVENT])
    counter = []
    bus.on(Topic.SAFETY_EVENT, counter.append)

    def worker(base: int) -> None:
        for n in range(500):
            bus.publish(Topic.SAFETY_EVENT, safety(base + n))

    threads = [threading.Thread(target=worker, args=(k * 1000,)) for k in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(counter) == 2000 and len(sub.drain()) == 2000


async def test_async_iteration_from_another_thread():
    bus = LocalBus()
    sub = bus.subscribe([Topic.ENGINE_STATUS, Topic.HOLD])
    threading.Timer(0.05, lambda: bus.publish(Topic.HOLD, hold())).start()
    topic, event = await asyncio.wait_for(sub.__anext__(), 2.0)
    assert topic is Topic.HOLD and event.source is HoldSource.HOTKEY
    sub.close()
    with pytest.raises(StopAsyncIteration):
        await sub.__anext__()


# ------------------------------------------------------------------ ZeroMQ broker


@pytest.fixture
def endpoints():
    d = tempfile.mkdtemp(prefix="ssb")
    yield f"ipc://{d}/out", f"ipc://{d}/in"
    shutil.rmtree(d, ignore_errors=True)


class Wire:
    """What ``app/api`` does: a SUB on the XPUB endpoint and a PUB on the XSUB endpoint."""

    def __init__(self, xpub: str, xsub: str, prefixes=(b"",)):
        self.ctx = zmq.Context()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.setsockopt(zmq.LINGER, 0)
        for p in prefixes:
            self.sub.setsockopt(zmq.SUBSCRIBE, p)
        self.sub.connect(xpub)
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.setsockopt(zmq.LINGER, 0)
        self.pub.connect(xsub)

    def recv(self, timeout: float = 1.0):
        if self.sub.poll(int(timeout * 1000)):
            return self.sub.recv_multipart()
        return None

    def recv_all(self, quiet: float = 0.3) -> list[list[bytes]]:
        out = []
        while (frames := self.recv(quiet)) is not None:
            out.append(frames)
        return out

    def publish(self, topic: Topic, event, *, raw: bytes | None = None, name: bytes | None = None) -> None:
        self.pub.send_multipart([name or topic.value.encode(), raw if raw is not None else dumps(event)])

    def publish_until(self, topic: Topic, event, done, timeout: float = 3.0) -> bool:
        """Retry until ``done()`` (a PUB drops messages sent before its connection is up)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.publish(topic, event)
            time.sleep(0.05)
            if done():
                return True
        return False

    def close(self) -> None:
        self.sub.close(0)
        self.pub.close(0)
        self.ctx.term()


def wait_for(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def rig(endpoints):
    xpub, xsub = endpoints
    bus = LocalBus()
    broker = ZmqBroker(bus, xpub_endpoint=xpub, xsub_endpoint=xsub)
    broker.start()
    wire = Wire(xpub, xsub)
    assert wait_for(lambda: b"" in broker._subscribed), "subscription did not reach the broker"
    yield bus, broker, wire
    wire.close()
    broker.stop()


def test_local_events_reach_a_subscriber_with_the_wire_codec(rig):
    bus, broker, wire = rig
    bus.publish(Topic.SAFETY_EVENT, safety(1))
    bus.publish(Topic.SAFETY_EVENT, safety(2))
    frames = [wire.recv(), wire.recv()]
    assert all(f is not None for f in frames)
    assert frames[0][0] == b"safety.event" and len(frames[0]) == 3  # [topic, epoch:seq envelope, json]
    assert loads(frames[0][-1]) == safety(1) and loads(frames[1][-1]) == safety(2)
    seqs = [int(f[1].split(b":")[1]) for f in frames]
    assert seqs[1] == seqs[0] + 1
    assert broker.stats["forwarded_out"] == 2


def test_local_only_topics_never_reach_the_wire(rig):
    bus, broker, wire = rig
    from contracts import MaskKind, MaskRequest, MaskStyle

    bus.publish(Topic.MUTE_SPAN, mute())
    bus.publish(
        Topic.MASK_REQUEST,
        MaskRequest(
            "r",
            "audio",
            Category.SPOKEN_PHONE,
            MaskKind.FULL_FRAME,
            (),
            1.0,
            1.0,
            2.0,
            MaskStyle.SOLID,
            0.9,
            "x",
            0.0,
        ),
    )
    bus.publish(Topic.SAFETY_EVENT, safety(9))  # a marker that does arrive
    frames = wire.recv_all()
    assert [f[0] for f in frames] == [b"safety.event"]


def test_local_only_topics_from_a_remote_publisher_are_rejected(rig):
    bus, broker, wire = rig
    delivered: list[object] = []
    bus.on(Topic.MUTE_SPAN, delivered.append)
    for _ in range(5):  # retry: the PUB may not be connected yet
        wire.publish(Topic.MUTE_SPAN, mute())
        time.sleep(0.05)
    wire.publish(Topic.SAFETY_EVENT, safety(1), raw=b"{}", name=b"not.a.topic")
    assert wait_for(lambda: broker.stats["rejected"] >= 1)
    assert wire.recv_all() == [] and delivered == []


def test_inbound_control_topics_reach_the_local_bus_and_other_subscribers(rig):
    bus, broker, wire = rig
    got: list[HoldCommand] = []
    configs: list[ConfigChange] = []
    bus.on(Topic.HOLD, got.append)
    bus.on(Topic.CONFIG, configs.append)
    sub = bus.subscribe([Topic.HOLD])
    assert wire.publish_until(Topic.HOLD, hold(True, HoldSource.API), lambda: bool(got))
    time.sleep(0.2)
    assert got[0] == hold(True, HoldSource.API)
    assert sub.get(0.5)[1].source is HoldSource.API
    wire.publish(Topic.CONFIG, ConfigChange({"categories.face.enabled": False}, "api"))
    assert wait_for(lambda: bool(configs)) and configs[0].diff == {"categories.face.enabled": False}
    # a second process on the wire sees the same hold (the broker passes it through)
    frames = wire.recv_all()
    assert any(f[0] == b"control.hold" for f in frames)


def test_a_remote_hold_is_not_echoed_back_and_a_local_hold_is_mirrored_once(rig):
    bus, broker, wire = rig
    got: list[HoldCommand] = []
    bus.on(Topic.HOLD, got.append)
    wire.publish_until(Topic.HOLD, hold(True, HoldSource.APP), lambda: bool(got))
    time.sleep(0.3)
    n_after_remote = len(got)
    assert n_after_remote >= 1
    wire.recv_all()  # drain the pass-through copies
    bus.publish(Topic.HOLD, hold(True, HoldSource.AUTO_TRANSPORT))  # e.g. the relay's ingest watchdog
    frames = wire.recv_all()
    assert [f[0] for f in frames] == [b"control.hold"]  # mirrored once for the app
    time.sleep(0.2)
    assert len(got) == n_after_remote + 1  # delivered locally once, never re-ingested from the wire


def test_malformed_and_mistyped_payloads_are_rejected(rig):
    bus, broker, wire = rig
    got: list[object] = []
    bus.on(Topic.HOLD, got.append)
    bus.on(Topic.CONFIG, got.append)
    start = broker.stats["rejected"]
    for _ in range(3):
        wire.publish(Topic.HOLD, None, raw=b"not json")
        wire.publish(Topic.HOLD, None, raw=dumps(ConfigChange({"a.b": 1}, "x")))  # right bytes, wrong type
        wire.publish(Topic.CONFIG, None, raw=b'{"t":"HoldCommand","v":2,"d":{}}')
        time.sleep(0.05)
    assert wait_for(lambda: broker.stats["rejected"] >= start + 3) and got == []


def test_no_encoding_work_without_subscribers(endpoints):
    xpub, xsub = endpoints
    bus = LocalBus()
    broker = ZmqBroker(bus, xpub_endpoint=xpub, xsub_endpoint=xsub)
    broker.start()
    try:
        for n in range(100):
            bus.publish(Topic.SAFETY_EVENT, safety(n))
        time.sleep(0.1)
        assert broker.stats["forwarded_out"] == 0 and len(broker._out) == 0
    finally:
        broker.stop()


def test_subscriber_filtering_by_topic_prefix(endpoints):
    xpub, xsub = endpoints
    bus = LocalBus()
    broker = ZmqBroker(bus, xpub_endpoint=xpub, xsub_endpoint=xsub)
    broker.start()
    wire = Wire(xpub, xsub, prefixes=(b"control.",))
    try:
        assert wait_for(lambda: b"control." in broker._subscribed)
        bus.publish(Topic.SAFETY_EVENT, safety(1))
        bus.publish(Topic.HOLD, hold())
        assert [f[0] for f in wire.recv_all()] == [b"control.hold"]
    finally:
        wire.close()
        broker.stop()


def test_endpoint_files_are_private_and_stop_cleans_up(endpoints):
    xpub, xsub = endpoints
    broker = ZmqBroker(LocalBus(), xpub_endpoint=xpub, xsub_endpoint=xsub)
    broker.start()
    try:
        for endpoint in (xpub, xsub):
            mode = stat.S_IMODE(os.stat(endpoint[len("ipc://") :]).st_mode)
            assert mode == 0o600
        assert broker.running
    finally:
        broker.stop()
    assert not broker.running
    broker.stop()  # idempotent


def test_bind_failure_raises_broker_error():
    broker = ZmqBroker(
        LocalBus(),
        xpub_endpoint="ipc:///nonexistent-dir-xyz/out",
        xsub_endpoint="ipc:///nonexistent-dir-xyz/in",
    )
    with pytest.raises(BrokerError):
        broker.start()


def test_only_wire_topics_can_be_inbound():
    with pytest.raises(ValueError):
        ZmqBroker(LocalBus(), inbound=[Topic.MUTE_SPAN])


def test_remote_hold_reaches_a_session_engine_over_ipc(endpoints):
    """The path the API and the hotkey helper use: ipc publisher -> broker -> LocalBus -> engine hold."""
    from contracts import Config
    from engine.deadline import DeadlineEngine
    from engine.motion import IdentityMotion
    from engine.session import Session
    from engine.session_fakes import RecordingSink, SyntheticSource

    xpub, xsub = endpoints
    config = Config(_env_file=None)
    bus = LocalBus()
    session = Session(
        config,
        SyntheticSource(3),
        RecordingSink(),
        bus=bus,
        engine=DeadlineEngine.from_config(config, (), motion=IdentityMotion()),
    )
    broker = ZmqBroker(bus, xpub_endpoint=xpub, xsub_endpoint=xsub)
    broker.start()
    wire = Wire(xpub, xsub)
    try:
        assert not session.engine.hold_active
        began = time.monotonic()
        assert wire.publish_until(Topic.HOLD, hold(True, HoldSource.APP), lambda: session.engine.hold_active)
        assert session.engine.control.snapshot().hold_sources == (HoldSource.APP,)
        wire.publish_until(Topic.HOLD, hold(False, HoldSource.APP), lambda: not session.engine.hold_active)
        assert not session.engine.hold_active
        assert time.monotonic() - began < 3.0
    finally:
        wire.close()
        broker.stop()
        session.engine.close()


def test_a_publish_only_process_is_heard_with_no_other_subscriber(endpoints):
    """The API publishes config changes but never subscribes to them: the engine must ask for them."""
    xpub, xsub = endpoints
    bus = LocalBus()
    broker = ZmqBroker(bus, xpub_endpoint=xpub, xsub_endpoint=xsub)
    broker.start()
    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.LINGER, 0)
    pub.connect(xsub)
    got: list[object] = []
    bus.on(Topic.CONFIG, got.append)
    bus.on(Topic.HOLD, got.append)
    try:
        end = time.monotonic() + 3.0
        while len(got) < 2 and time.monotonic() < end:
            pub.send_multipart([b"control.config", dumps(ConfigChange({"hold.retro_seconds": 2.0}, "api"))])
            pub.send_multipart([b"control.hold", dumps(hold(True, HoldSource.API))])
            time.sleep(0.05)
        assert len(got) >= 2
    finally:
        pub.close(0)
        ctx.term()
        broker.stop()
