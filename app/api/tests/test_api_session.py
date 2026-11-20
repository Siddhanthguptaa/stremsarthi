"""POST /api/session/start|stop, GET /api/session: validation, publishing and the state view."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.sessions import SessionManager
from app.api.tests.conftest import FAKE_GROQ_KEY, FAKE_TW_KEY, FAKE_YT_KEY
from contracts import ConfigChange, SessionCommand, Topic

YT = {"kind": "youtube", "enabled": True}
TW = {"kind": "twitch", "enabled": True}


def commands(bus) -> list[SessionCommand]:
    return [e for t, e in bus.bus.history if t is Topic.SESSION]


@pytest.fixture
def keyed(settings_factory, bus, config_with_keys):
    app = create_app(settings_factory(), bus, config_with_keys)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        yield c


def set_engine(client: TestClient, publish, sample, wait_until, state: str) -> None:
    publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state=state))
    assert wait_until(lambda: client.get("/api/status").json()["engine"]["d"]["state"] == state)


# --- roles ---------------------------------------------------------------------------------------


def test_session_routes_are_streamer_only(client: TestClient, role_headers, bus):
    for method, path in (
        ("GET", "/api/session"),
        ("POST", "/api/session/start"),
        ("POST", "/api/session/stop"),
    ):
        assert client.request(method, path, json={}).status_code == 401
        for role in ("moderator", "hotkey", "viewer-readonly"):
            assert client.request(method, path, json={}, headers=role_headers(role)).status_code == 403
    assert commands(bus) == []


# --- start validation ----------------------------------------------------------------------------


def test_start_without_an_enabled_destination_is_422(keyed: TestClient, bus):
    for body in ({}, {"destinations": []}, {"destinations": [{"kind": "youtube", "enabled": False}]}):
        r = keyed.post("/api/session/start", json=body)
        assert r.status_code == 422 and r.json()["error"] == "no_destination"
    assert commands(bus) == []
    assert keyed.get("/api/session").json()["state"] == "idle"


def test_start_with_a_missing_key_names_the_key_not_a_value(client: TestClient, role_headers, bus):
    headers = role_headers("streamer")
    r = client.post("/api/session/start", json={"destinations": [YT, TW]}, headers=headers)
    assert r.status_code == 422 and r.json()["error"] == "missing_keys"
    assert "youtube_stream_key" in r.json()["detail"] and "twitch_stream_key" in r.json()["detail"]
    assert commands(bus) == []


def test_start_checks_only_the_enabled_destinations(settings_factory, bus):
    from contracts import Config

    cfg = Config(_env_file=None, twitch_stream_key=FAKE_TW_KEY)  # type: ignore[arg-type]
    app = create_app(settings_factory(), bus, cfg)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        h = {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
        both = c.post("/api/session/start", json={"destinations": [YT, TW]}, headers=h)
        assert both.status_code == 422 and "youtube_stream_key" in both.json()["detail"]
        assert "twitch_stream_key" not in both.json()["detail"]
        only_tw = c.post(
            "/api/session/start",
            json={"destinations": [{"kind": "youtube", "enabled": False}, TW]},
            headers=h,
        )
        assert only_tw.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"destinations": [YT], "delay_seconds": 2.9},
        {"destinations": [YT], "delay_seconds": 5.1},
        {"destinations": [{"kind": "rumble", "enabled": True}]},
        {"destinations": [YT], "profile": "prod"},
    ],
)
def test_start_validates_the_request(keyed: TestClient, bus, body):
    assert keyed.post("/api/session/start", json=body).status_code == 422
    assert commands(bus) == []


# --- start and stop ------------------------------------------------------------------------------


def test_start_publishes_a_session_command_and_reports_checks(keyed: TestClient, bus):
    r = keyed.post("/api/session/start", json={"destinations": [YT, {"kind": "twitch", "enabled": False}]})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "starting" and len(body["session_id"]) >= 8
    assert body["checks"] == {
        "youtube_key": True,
        "twitch_key": True,
        "groq_key": True,
        "sarvam_key": False,
        "gpu": False,
    }
    assert [c.action for c in commands(bus)] == ["start"]
    assert len(commands(bus)[0].actor) == 16
    for secret in (FAKE_YT_KEY, FAKE_TW_KEY, FAKE_GROQ_KEY):
        assert secret not in r.text

    s = keyed.get("/api/session").json()
    assert s["session_id"] == body["session_id"] and s["state"] == "starting"
    assert s["profile"] == "dev" and s["hold"] == {"active": False, "sources": []}
    assert set(s["ingest"]) == {"srt_url", "rtmp_url"}


def test_start_applies_delay_and_destinations_and_publishes_the_delay(keyed: TestClient, bus):
    r = keyed.post("/api/session/start", json={"destinations": [YT], "delay_seconds": 3.5})
    assert r.status_code == 200
    cfg = keyed.get("/api/config").json()
    assert cfg["delay_seconds"] == 3.5
    assert {d["kind"]: d["enabled"] for d in cfg["destinations"]} == {
        "youtube": True,
        "twitch": False,
        "mediamtx_tap": True,
    }
    assert keyed.get("/api/session").json()["delay_seconds"] == 3.5
    changes = [e for t, e in bus.bus.history if t is Topic.CONFIG]
    assert (
        len(changes) == 1
        and isinstance(changes[0], ConfigChange)
        and dict(changes[0].diff) == {"delay_seconds": 3.5}
    )


def test_a_second_start_while_active_is_409(keyed: TestClient, bus):
    assert keyed.post("/api/session/start", json={"destinations": [YT]}).status_code == 200
    r = keyed.post("/api/session/start", json={"destinations": [YT]})
    assert r.status_code == 409 and r.json()["error"] == "session_active"
    assert [c.action for c in commands(bus)] == ["start"]


def test_stop_publishes_and_the_session_ends_when_the_engine_does(
    keyed: TestClient, bus, publish, sample, wait_until
):
    keyed.post("/api/session/start", json={"destinations": [YT]})
    set_engine(keyed, publish, sample, wait_until, "live")
    assert keyed.get("/api/session").json()["state"] == "live"

    r = keyed.post("/api/session/stop")
    assert r.status_code == 200 and r.json() == {"state": "stopping"}
    assert [c.action for c in commands(bus)] == ["start", "stop"]
    assert keyed.get("/api/session").json()["state"] == "stopping"

    set_engine(keyed, publish, sample, wait_until, "ready")
    assert keyed.get("/api/session").json()["state"] == "ended"
    # an ended session no longer blocks the next one
    assert keyed.post("/api/session/start", json={"destinations": [YT]}).status_code == 200


def test_publish_failure_does_not_start_a_session(settings_factory, bus, config_with_keys, monkeypatch):
    app = create_app(settings_factory(), bus, config_with_keys)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        monkeypatch.setattr(bus, "publish", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
        assert c.post("/api/session/start", json={"destinations": [YT]}).status_code == 503
        assert c.get("/api/session").json()["state"] == "idle"


# --- state machine -------------------------------------------------------------------------------


def test_state_follows_the_engine(keyed: TestClient, publish, sample, wait_until):
    keyed.post("/api/session/start", json={"destinations": [YT]})
    expected = {"ready": "starting", "waiting_ingest": "waiting_ingest", "live": "live", "error": "failed"}
    for engine_state, session_state in expected.items():
        set_engine(keyed, publish, sample, wait_until, engine_state)
        assert keyed.get("/api/session").json()["state"] == session_state


def test_engine_loss_and_start_timeout(sample):
    status = sample("EngineStatus")
    live = replace(status, state="live")

    lost = SessionManager(start_timeout_s=30)
    lost.start(4.0, "dev", {"youtube": True})
    assert lost.state(live) == "live"
    assert lost.state(None) == "engine_lost"  # heartbeats stopped after the stream was live
    assert lost.is_active(None)
    assert lost.state(live) == "live"  # and it recovers

    slow = SessionManager(start_timeout_s=0.01)
    slow.start(4.0, "dev", {"youtube": True})
    assert slow.state(None) == "starting"
    time.sleep(0.03)
    assert slow.state(None) == "failed"
    assert not slow.is_active(None)


def test_state_without_a_session(sample):
    mgr = SessionManager()
    status = sample("EngineStatus")
    assert mgr.state(None) == "idle"
    assert mgr.state(replace(status, state="ready")) == "idle"
    assert mgr.state(replace(status, state="live")) == "live"  # engine started outside the API
    assert mgr.is_active(replace(status, state="waiting_ingest"))
    assert not mgr.is_active(replace(status, state="ready"))


def test_stop_with_no_session_still_publishes(keyed: TestClient, bus):
    assert keyed.post("/api/session/stop").json() == {"state": "stopping"}
    assert [c.action for c in commands(bus)] == ["stop"]
