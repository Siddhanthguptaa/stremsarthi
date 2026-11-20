"""Bus clients: in-process adapter rules and a real ZeroMQ round trip through an XSUB/XPUB proxy."""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest
import zmq

from app.api.bus import API_TOPICS, InProcessBusClient, ZmqBusClient
from contracts import WIRE_TOPICS, HoldCommand, HoldSource, Topic, dumps


def test_api_topics_are_wire_topics():
    assert set(API_TOPICS) <= set(WIRE_TOPICS)
    assert Topic.CHAT_MODQUEUE in API_TOPICS and Topic.CHAT_MODACTION not in API_TOPICS


def test_inprocess_client_rejects_local_only_and_wrong_types(sample):
    bus = InProcessBusClient()
    with pytest.raises(ValueError):
        bus.publish(Topic.DETECTION, sample("Detection"))
    with pytest.raises(ValueError):
        bus.subscribe([Topic.TRANSCRIPT])
    with pytest.raises(TypeError):
        bus.publish(Topic.HOLD, sample("SafetyEvent"))
    bus.publish(Topic.HOLD, HoldCommand(source=HoldSource.API, active=True, actor="x"))
    assert len(bus.bus.of(Topic.HOLD)) == 1


class Broker:
    """Minimal XSUB/XPUB proxy, like engine/bus.py ZmqBroker (test double)."""

    def __init__(self, sub_ep: str, pub_ep: str) -> None:
        self.ctx = zmq.Context()
        self.xsub = self.ctx.socket(zmq.XSUB)
        self.xsub.bind(pub_ep)
        self.xpub = self.ctx.socket(zmq.XPUB)
        self.xpub.bind(sub_ep)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        poller = zmq.Poller()
        poller.register(self.xsub, zmq.POLLIN)
        poller.register(self.xpub, zmq.POLLIN)
        while not self.stop.is_set():
            ready = dict(poller.poll(20))
            if self.xsub in ready:
                self.xpub.send_multipart(self.xsub.recv_multipart())
            if self.xpub in ready:
                self.xsub.send_multipart(self.xpub.recv_multipart())  # subscription frames

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        self.xsub.close(linger=0)
        self.xpub.close(linger=0)
        self.ctx.term()


@pytest.fixture
def endpoints(tmp_path_factory):
    base = f"/tmp/ss-test-{uuid.uuid4().hex[:8]}"
    return f"ipc://{base}-out", f"ipc://{base}-in"


async def _next(sub, wait: float = 3.0):
    return await asyncio.wait_for(sub.__anext__(), wait)


async def test_zmq_round_trip_and_rejections(endpoints, sample):
    sub_ep, pub_ep = endpoints
    client = ZmqBusClient(sub_ep, pub_ep)
    assert client.connected is False  # not started
    broker = Broker(sub_ep, pub_ep)
    raw = zmq.Context.instance().socket(zmq.PUB)
    raw.connect(pub_ep)
    try:
        await client.start()
        assert client.connected is True
        sub = client.subscribe([Topic.HOLD, Topic.SAFETY_EVENT])
        hold = HoldCommand(source=HoldSource.API, active=True, actor="abc")

        # slow-joiner: publish until the subscription has propagated through the proxy
        got = None
        for _ in range(100):
            client.publish(Topic.HOLD, hold)
            try:
                got = await _next(sub, 0.05)
                break
            except TimeoutError:
                continue
        assert got == (Topic.HOLD, hold)

        # frames that break the contract are rejected and counted, the loop keeps running
        before = client.rejected
        raw.send_multipart([b"safety.event", b"{not json"])
        raw.send_multipart([b"safety.event", dumps(hold)])  # valid wire object, wrong type for the topic
        raw.send_multipart([b"safety.event", b"x" * (300 * 1024)])  # over the 256 KB limit
        raw.send_multipart([b"safety.event", dumps(sample("SafetyEvent"))])
        topic, event = await _next(sub)
        assert topic is Topic.SAFETY_EVENT and event == sample("SafetyEvent")
        assert client.rejected - before == 3

        # an extra envelope frame between topic and payload is tolerated
        raw.send_multipart([b"control.hold", b"seq-1", dumps(hold)])
        assert (await _next(sub))[0] is Topic.HOLD

        with pytest.raises(ValueError):
            client.publish(Topic.DETECTION, sample("Detection"))
        with pytest.raises(TypeError):
            client.publish(Topic.HOLD, sample("SafetyEvent"))
        with pytest.raises(ValueError):
            client.subscribe([Topic.CHAT_MODACTION])  # wire topic, but not one this client reads
    finally:
        raw.close(linger=0)
        await client.stop()
        broker.close()


async def test_zmq_publish_requires_start(endpoints):
    client = ZmqBusClient(*endpoints)
    with pytest.raises(RuntimeError):
        client.publish(Topic.HOLD, HoldCommand(source=HoldSource.API, active=True, actor="abc"))


async def test_zmq_client_without_broker_is_not_connected_and_does_not_raise(endpoints):
    client = ZmqBusClient(*endpoints)
    await client.start()
    try:
        assert client.connected is False  # broker's ipc sockets do not exist
        client.publish(Topic.HOLD, HoldCommand(source=HoldSource.API, active=True, actor="abc"))
    finally:
        await client.stop()


def test_zmq_client_refuses_local_only_topics():
    with pytest.raises(ValueError):
        ZmqBusClient("ipc:///tmp/x-out", "ipc:///tmp/x-in", topics=[Topic.TRANSCRIPT])
