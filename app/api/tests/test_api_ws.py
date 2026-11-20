"""WebSocket hub: auth, ACL, echo through the in-process bus, replay, heartbeat, backpressure."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.main import create_app
from app.api.ws import CLOSE_LAGGING, Client, event_visible_to
from contracts import (
    Category,
    HoldCommand,
    HoldSource,
    Modality,
    SafetyEvent,
    Topic,
    loads,
)

H = {"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:8000"}  # TestClient WS defaults Host to testserver


def open_ws(client: TestClient, token: str, topics: list[str] | None = None):
    ws = client.websocket_connect("/api/ws", headers=H)
    conn = ws.__enter__()
    conn.send_text(json.dumps({"type": "auth", "token": token}))
    if topics is not None:
        conn.send_text(json.dumps({"type": "subscribe", "topics": topics}))
        assert conn.receive_json()["type"] == "subscribed"
    return ws, conn


def frame(conn) -> dict[str, Any]:
    return json.loads(conn.receive_text())


def test_echo_through_in_process_bus(client, streamer_token, publish, sample):
    ws, conn = open_ws(client, streamer_token, ["safety.event", "guard.risk"])
    try:
        event = sample("SafetyEvent")
        publish(Topic.SAFETY_EVENT, event)
        f = frame(conn)
        assert f["topic"] == "safety.event"
        assert isinstance(f["seq"], int) and f["ts"].endswith("+00:00")
        assert f["data"]["t"] == "SafetyEvent"
        assert loads(json.dumps(f["data"])) == event  # wire-exact round trip

        publish(Topic.RISK, sample("RiskSignal"))
        publish(Topic.SAFETY_EVENT, event)
        seqs = [frame(conn)["seq"], frame(conn)["seq"]]
        assert seqs == sorted(seqs) and len(set(seqs)) == 2
    finally:
        ws.__exit__(None, None, None)


def test_unsubscribed_topics_are_not_delivered(client, streamer_token, publish, sample):
    ws, conn = open_ws(client, streamer_token, ["guard.risk"])
    try:
        publish(Topic.SAFETY_EVENT, sample("SafetyEvent"))
        publish(Topic.RISK, sample("RiskSignal"))
        assert frame(conn)["topic"] == "guard.risk"
    finally:
        ws.__exit__(None, None, None)


def test_no_auth_frame_closes_4401(app, settings_factory, bus):
    quick = create_app(settings_factory(ws_auth_timeout_s=0.2), bus)
    with TestClient(quick, base_url="http://127.0.0.1:8000") as c:
        with c.websocket_connect("/api/ws", headers=H) as conn:
            with pytest.raises(WebSocketDisconnect) as exc:
                conn.receive_text()
            assert exc.value.code == 4401


@pytest.mark.parametrize("payload", ['{"type":"auth","token":"nope"}', '{"type":"hello"}', "not json"])
def test_bad_auth_closes_4401(client, payload):
    with client.websocket_connect("/api/ws", headers=H) as conn:
        conn.send_text(payload)
        with pytest.raises(WebSocketDisconnect) as exc:
            conn.receive_text()
        assert exc.value.code == 4401


def test_streamer_cannot_subscribe_to_modqueue(client, streamer_token):
    ws, conn = open_ws(client, streamer_token)
    try:
        conn.send_text(json.dumps({"type": "subscribe", "topics": ["safety.event", "chat.modqueue"]}))
        err = conn.receive_json()
        assert err == {"type": "error", "code": "forbidden_topics", "topics": ["chat.modqueue"]}
        conn.send_text(json.dumps({"type": "subscribe", "topics": ["telemetry.timing"]}))
        assert conn.receive_json()["code"] == "forbidden_topics"
        conn.send_text(json.dumps({"type": "subscribe", "topics": ["no.such.topic"]}))
        assert conn.receive_json()["code"] == "unknown_topic"
    finally:
        ws.__exit__(None, None, None)


def test_moderator_gets_modqueue_and_only_chat_safety_events(client, app, publish, sample):
    token = app.state.tokens.issue("moderator")
    ws, conn = open_ws(client, token, ["chat.modqueue", "safety.event"])
    try:
        assert conn.receive_json() == {  # replay marker: a late console resets, then gets the pending queue
            "type": "replay",
            "topic": "chat.modqueue",
            "count": 0,
            "hidden_count": 0,
            "pending_count": 0,
        }
        publish(Topic.CHAT_MODQUEUE, sample("ModQueueItem"))
        assert frame(conn)["topic"] == "chat.modqueue"

        video = sample("SafetyEvent")
        assert video.modality is Modality.VIDEO
        publish(Topic.SAFETY_EVENT, video)
        chat = SafetyEvent(**{**_fields(video), "modality": Modality.CHAT, "category": Category.CHAT_ABUSE})
        publish(Topic.SAFETY_EVENT, chat)
        f = frame(conn)  # the video event was filtered out
        assert f["topic"] == "safety.event" and f["data"]["d"]["modality"] == "chat"
    finally:
        ws.__exit__(None, None, None)


def _fields(obj: Any) -> dict[str, Any]:
    from dataclasses import fields

    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def test_hotkey_role_gets_no_topics(client, app):
    ws, conn = open_ws(client, app.state.tokens.issue("hotkey"))
    try:
        conn.send_text(json.dumps({"type": "subscribe", "topics": ["control.hold"]}))
        assert conn.receive_json()["code"] == "forbidden_topics"
    finally:
        ws.__exit__(None, None, None)


def test_streamer_chat_verdict_has_no_text(client, streamer_token, publish, sample):
    ws, conn = open_ws(client, streamer_token, ["chat.verdict"])
    try:
        assert conn.receive_json()["type"] == "replay"  # the hidden count so far
        publish(Topic.CHAT_VERDICT, sample("ChatVerdict"))
        data = frame(conn)["data"]["d"]
        assert "text" not in data and "author_display" not in data
    finally:
        ws.__exit__(None, None, None)


def test_late_joiner_gets_status_and_hold_replay(client, app, streamer_token, publish, sample, wait_until):
    publish(Topic.ENGINE_STATUS, sample("EngineStatus"))
    publish(Topic.HOLD, HoldCommand(source=HoldSource.HOTKEY, active=True, actor="abc"))
    publish(Topic.HOLD, HoldCommand(source=HoldSource.APP, active=True, actor="abc"))
    publish(Topic.HOLD, HoldCommand(source=HoldSource.APP, active=False, actor="abc"))
    assert wait_until(lambda: app.state.hub.hold_sources == {HoldSource.HOTKEY})
    ws, conn = open_ws(client, streamer_token, ["engine.status", "control.hold"])
    try:
        got = {f["topic"]: f for f in (frame(conn), frame(conn))}
        assert got["engine.status"]["data"]["t"] == "EngineStatus"
        assert got["control.hold"]["data"]["d"]["source"] == "hotkey"
        assert got["control.hold"]["data"]["d"]["active"] is True
    finally:
        ws.__exit__(None, None, None)


def test_socket_is_read_only(client, streamer_token, publish, sample, bus):
    ws, conn = open_ws(client, streamer_token, ["control.hold"])
    try:
        before = len(bus.bus.history)
        conn.send_text(json.dumps({"type": "hold", "active": True}))
        conn.send_text(json.dumps({"topic": "control.hold", "data": {}}))
        time.sleep(0.1)
        assert len(bus.bus.history) == before  # nothing the client says reaches the bus
    finally:
        ws.__exit__(None, None, None)


def test_server_pings_and_closes_after_two_missed(settings_factory, bus, streamer_token_for):
    app = create_app(settings_factory(ws_ping_interval_s=0.1), bus)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        ws, conn = open_ws(c, streamer_token_for(app), [])
        try:
            pings = 0
            with pytest.raises(WebSocketDisconnect) as exc:
                while True:
                    if conn.receive_json() == {"type": "ping"}:
                        pings += 1
            assert pings == 2 and exc.value.code == CLOSE_LAGGING
        finally:
            ws.__exit__(None, None, None)


def test_pong_keeps_the_connection_alive(settings_factory, bus, streamer_token_for):
    app = create_app(settings_factory(ws_ping_interval_s=0.3), bus)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        ws, conn = open_ws(c, streamer_token_for(app), [])
        try:
            for _ in range(4):
                assert conn.receive_json() == {"type": "ping"}
                conn.send_text('{"type":"pong"}')
        finally:
            ws.__exit__(None, None, None)


def test_expired_token_closes_4401(client, app):
    token = app.state.tokens.issue("streamer", ttl_seconds=1)
    ws, conn = open_ws(client, token, [])
    try:
        with pytest.raises(WebSocketDisconnect) as exc:
            conn.receive_text()
        assert exc.value.code == 4401
    finally:
        ws.__exit__(None, None, None)


def test_never_binds_hub_to_local_only_topics(client, streamer_token):
    ws, conn = open_ws(client, streamer_token)
    try:
        conn.send_text(json.dumps({"type": "subscribe", "topics": ["video.detection", "audio.transcript"]}))
        assert conn.receive_json()["code"] == "forbidden_topics"
    finally:
        ws.__exit__(None, None, None)


# --- backpressure (unit level, no sockets) ------------------------------------------------


def _client(queue_max: int = 500, lag: float = 5.0) -> Client:
    class _Principal:
        role = "streamer"

    return Client(ws=None, principal=_Principal(), queue_max=queue_max, lag_close_s=lag)  # type: ignore[arg-type]


def test_slow_client_drops_oldest_telemetry_but_never_hold():
    c = _client()
    now = time.monotonic()
    c.push(Topic.HOLD, "hold-1", now)
    c.push(Topic.ENGINE_STATUS, "status-1", now)
    for i in range(1000):
        c.push(Topic.SAFETY_EVENT, f"evt-{i}", now)
    texts = [t for _, _, t in c.dq]
    assert len(texts) == 500
    assert "hold-1" in texts and "status-1" in texts
    assert "evt-0" not in texts and "evt-999" in texts  # oldest telemetry went first
    assert c.dropped == 502
    assert c.close_code is None


def test_hold_survives_a_full_queue_of_holds():
    c = _client(queue_max=3)
    now = time.monotonic()
    for i in range(6):
        c.push(Topic.HOLD, f"h{i}", now)
    assert [t for _, _, t in c.dq] == [f"h{i}" for i in range(6)]


def test_client_lagging_five_seconds_is_closed_4408():
    now = time.monotonic()
    c = _client(lag=5.0)
    c.push(Topic.SAFETY_EVENT, "old", now - 5.5)
    c.check_lag(now)
    assert c.close_code == CLOSE_LAGGING
    fresh = _client(lag=5.0)
    fresh.push(Topic.SAFETY_EVENT, "recent", now - 4.0)
    fresh.check_lag(now)
    assert fresh.close_code is None


def test_visibility_filter():
    class E:
        modality = Modality.AUDIO

    assert event_visible_to("streamer", Topic.SAFETY_EVENT, E())
    assert not event_visible_to("moderator", Topic.SAFETY_EVENT, E())
