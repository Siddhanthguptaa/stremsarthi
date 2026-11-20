"""Acceptance (T1.6): a hold click through the real API and the real ZeroMQ client reaches the
``control.hold`` topic of a test XSUB/XPUB proxy with ``HoldSource`` set correctly."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import zmq
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.settings import ApiSettings
from contracts import HoldCommand, HoldSource, SessionCommand, Topic, loads

SECRET = "test-secret-key-0123456789"


class Broker:
    """XSUB/XPUB proxy with the same two endpoints as engine/bus.py (D-35)."""

    def __init__(self, sub_ep: str, pub_ep: str) -> None:
        self.ctx = zmq.Context()
        self.xsub = self.ctx.socket(zmq.XSUB)
        self.xsub.bind(pub_ep)
        self.xpub = self.ctx.socket(zmq.XPUB)
        self.xpub.bind(sub_ep)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        poller = zmq.Poller()
        poller.register(self.xsub, zmq.POLLIN)
        poller.register(self.xpub, zmq.POLLIN)
        while not self.stop.is_set():
            ready = dict(poller.poll(20))
            if self.xsub in ready:
                self.xpub.send_multipart(self.xsub.recv_multipart())
            if self.xpub in ready:
                self.xsub.send_multipart(self.xpub.recv_multipart())

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        self.xsub.close(linger=0)
        self.xpub.close(linger=0)
        self.ctx.term()


@dataclass
class Stack:
    client: TestClient
    app: object
    listener: zmq.Socket
    sub_ep: str
    pub_ep: str

    def headers(self, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.app.state.tokens.issue(role)}"}  # type: ignore[attr-defined]

    def drain(self, topic: Topic, want: int, wait: float = 3.0) -> list[object]:
        """Messages on ``topic`` as the engine would see them (raw frames, strict contract decode)."""
        out: list[object] = []
        end = time.monotonic() + wait
        while len(out) < want and time.monotonic() < end:
            if self.listener.poll(50):
                frames = self.listener.recv_multipart()
                assert frames[0] == topic.value.encode()
                out.append(loads(frames[-1]))
        return out


@pytest.fixture
def stack() -> Iterator[Stack]:
    base = f"/tmp/ss-test-{uuid.uuid4().hex[:8]}"
    sub_ep, pub_ep = f"ipc://{base}-out", f"ipc://{base}-in"
    broker = Broker(sub_ep, pub_ep)
    settings = ApiSettings(
        _env_file=None, secret_key=SECRET, bus_backend="zmq", bus_sub_endpoint=sub_ep, bus_pub_endpoint=pub_ep
    )  # type: ignore[call-arg]
    app = create_app(settings)
    ctx = zmq.Context()
    listener = ctx.socket(zmq.SUB)  # stands in for the engine: subscribes through the broker's XPUB side
    listener.setsockopt(zmq.LINGER, 0)
    for topic in (Topic.HOLD, Topic.SESSION):
        listener.setsockopt(zmq.SUBSCRIBE, topic.value.encode())
    listener.connect(sub_ep)
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            warm_up(client, app, listener)
            yield Stack(client, app, listener, sub_ep, pub_ep)
    finally:
        listener.close(linger=0)
        ctx.term()
        broker.close()


def warm_up(client: TestClient, app, listener: zmq.Socket) -> None:
    """Wait until the subscription has propagated listener -> XPUB -> XSUB -> the API's PUB socket,
    by releasing a hold (a harmless command) until it arrives; then drop what was received."""
    headers = {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        client.post("/api/hold", json={"active": False, "source": "api"}, headers=headers)
        if listener.poll(100):
            break
        app.state.hold_release_limiter._hits.clear()  # the warm-up must not eat the release budget
    else:
        pytest.fail("ZeroMQ path did not come up")
    time.sleep(0.2)
    while listener.poll(50):
        listener.recv_multipart()
    app.state.hold_release_limiter._hits.clear()


@pytest.mark.parametrize(
    ("role", "body", "source"),
    [
        ("streamer", {"source": "app"}, HoldSource.APP),  # the web app's HOLD button
        ("streamer", {}, HoldSource.API),  # a script with the streamer token
        ("moderator", {}, HoldSource.API),
        ("hotkey", {}, HoldSource.HOTKEY),  # the Windows hotkey helper
    ],
)
def test_hold_click_reaches_control_hold_with_the_right_source(stack: Stack, role, body, source):
    r = stack.client.post("/api/hold", json={"active": True, **body}, headers=stack.headers(role))
    assert r.status_code == 202  # no engine in this test: accepted, published, unconfirmed
    (got,) = stack.drain(Topic.HOLD, 1)
    assert isinstance(got, HoldCommand)
    assert got.source is source and got.active is True
    assert len(got.actor) == 16 and got.actor.isalnum()  # an opaque hash: no token material


def test_release_and_session_commands_reach_the_bus_too(stack: Stack):
    streamer = stack.headers("streamer")
    stack.client.post("/api/hold", json={"active": True, "source": "app"}, headers=streamer)
    stack.client.post("/api/hold", json={"active": False, "source": "app"}, headers=streamer)
    held, released = stack.drain(Topic.HOLD, 2)
    assert (held.source, held.active, released.source, released.active) == (  # type: ignore[attr-defined]
        HoldSource.APP,
        True,
        HoldSource.APP,
        False,
    )

    stack.client.post("/api/session/stop", headers=streamer)
    (cmd,) = stack.drain(Topic.SESSION, 1)
    assert isinstance(cmd, SessionCommand) and cmd.action == "stop"


def test_hold_state_comes_back_over_the_bus_and_the_websocket(stack: Stack):
    """The API sees its own command through the broker (the UI's confirmation path)."""
    import json

    token = stack.app.state.tokens.issue("streamer")  # type: ignore[attr-defined]
    headers = {"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:8000"}
    with stack.client.websocket_connect("/api/ws", headers=headers) as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        ws.send_text(json.dumps({"type": "subscribe", "topics": ["control.hold"]}))
        assert ws.receive_json()["type"] == "subscribed"
        stack.client.post(
            "/api/hold", json={"active": True, "source": "app"}, headers=stack.headers("streamer")
        )
        frame = json.loads(ws.receive_text())
        assert frame["topic"] == "control.hold"
        assert frame["data"]["d"] == {"source": "app", "active": True, "actor": frame["data"]["d"]["actor"]}
    assert stack.client.get("/api/hold", headers=stack.headers("streamer")).json()["sources"] == ["app"]
