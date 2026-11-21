"""Go-live flow through the API with harmless child processes: keys, spawn, health, pairing, stop, report."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tests.conftest import FAKE_TW_KEY, FAKE_YT_KEY
from contracts import Category, Config, Modality, SafetyEvent, Topic

PY = sys.executable
SLEEP_CMD = f'{PY} -c "import time; time.sleep(60)"'
CHAT_CMD = f"{PY} -c \"import time; print('groq_key_missing twitch_no_channel', flush=True); time.sleep(60)\""
YT = {"kind": "youtube", "enabled": True}
TW = {"kind": "twitch", "enabled": True}
KEY_TYPED = "typed-YT-KEY-abcdef12"


@pytest.fixture
def spawning(settings_factory, bus, tmp_path):
    def make(config: Config | None = None, **settings):
        base = dict(
            session_spawn=True, session_engine_cmd=SLEEP_CMD, session_chat_cmd=CHAT_CMD,
            session_stop_timeout_s=3.0, sessions_dir=str(tmp_path / "sessions"),
        )  # fmt: skip
        base.update(settings)
        return create_app(settings_factory(**base), bus, config or Config(_env_file=None))

    return make


def open_client(app) -> TestClient:
    c = TestClient(app, base_url="http://127.0.0.1:8000")
    c.__enter__()
    c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
    return c


def wait(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def safety_event() -> SafetyEvent:
    return SafetyEvent(
        event_id="ev000001", session_id="engine-s", wall_ts=datetime.now(UTC).isoformat(timespec="seconds"),
        pts=1.0, modality=Modality.AUDIO, category=Category.SPOKEN_PHONE, action="muted", confidence=0.91,
        bbox=None, duration_ms=800, source="audio", detail_code="ok",
    )  # fmt: skip


def test_full_go_live_flow_with_typed_key_chat_pairing_and_report(spawning, publish, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    cfg = Config(_env_file=None, chat={"enabled": True})  # no stream key in the environment
    app = spawning(cfg)
    c = open_client(app)
    bodies: list[str] = []

    def call(method: str, path: str, **kw):
        r = c.request(method, path, **kw)
        bodies.append(r.text)
        return r

    try:
        # destinations: no key yet, so a start is refused with key names only
        assert call("GET", "/api/destinations").json()["destinations"][0]["configured"] is False
        r = call("POST", "/api/session/start", json={"destinations": [YT]})
        assert r.status_code == 422 and r.json()["error"] == "missing_keys" and "youtube_stream_key" in r.text
        assert (
            call("PUT", "/api/destinations/youtube/key", json={"key": "has spaces in it!"}).status_code == 422
        )
        assert call("PUT", "/api/destinations/youtube/key", json={"key": "short"}).status_code == 422
        ok = call("PUT", "/api/destinations/youtube/key", json={"key": KEY_TYPED})
        assert ok.json() == {"kind": "youtube", "configured": True, "key_source": "session"}
        assert call("GET", "/api/destinations").json()["destinations"][0]["key_source"] == "session"

        # pairing needs a session (the publish login is per session)
        assert call("GET", "/api/pairing").status_code == 409

        r = call("POST", "/api/session/start", json={"destinations": [YT]})
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        assert r.json()["checks"]["youtube_key"] is True
        assert call("POST", "/api/session/start", json={"destinations": [YT]}).status_code == 409

        session = call("GET", "/api/session").json()
        assert session["ingest"]["rtmp_url"].startswith("rtmp://127.0.0.1:1935/camera?user=")
        assert session["ingest"]["srt_url"].startswith("srt://127.0.0.1:8890?streamid=publish:camera:")
        assert session["processes"]["engine"]["state"] == "running"
        assert wait(lambda: call("GET", "/api/session/health").json()["chat"]["status_codes"])
        health = call("GET", "/api/session/health").json()
        assert health["chat"]["process"] == "running" and health["ready"] is False  # no engine heartbeat yet
        assert health["chat"]["status_codes"] == ["groq_key_missing", "twitch_no_channel"]
        assert health["engine"]["heartbeat"] is False

        # the child got the typed key in its environment, not on its command line
        proc_env = app.state.orchestrator.engine._env
        assert proc_env["YOUTUBE_STREAM_KEY"] == KEY_TYPED and proc_env["INGEST_PUBLISH_USER"]
        assert KEY_TYPED not in " ".join(app.state.orchestrator.engine.argv)
        assert "YOUTUBE_STREAM_KEY" not in app.state.orchestrator.chat._env

        # pairing (relay helper or the local fallback): streamer only, login matches the session
        pair = call("GET", "/api/pairing").json()
        assert pair["rtmp"]["url"] == session["ingest"]["rtmp_url"] and "key" not in pair["rtmp"]
        assert pair["moblin_url"].startswith("moblin://?") and pair["srt"]["url"].startswith("srt://")
        png = c.get("/api/pairing/rtmp.png")
        assert (
            png.status_code == 200
            and png.content[:4] == b"\x89PNG"
            and png.headers["cache-control"] == "no-store"
        )
        assert c.get("/api/pairing/srt.png").content[:4] == b"\x89PNG"
        assert c.get("/api/pairing/ftp.png").status_code == 422
        mod = {"Authorization": f"Bearer {app.state.tokens.issue('moderator')}"}
        assert c.get("/api/pairing", headers=mod).status_code == 403
        assert c.get("/api/pairing/rtmp.png", headers={"Authorization": ""}).status_code == 401

        # events recorded during the session
        publish(Topic.SAFETY_EVENT, safety_event())
        assert wait(lambda: len(app.state.recorder.current.events) == 1)

        # stop: returns at once, children stop, report appears, state ends
        t0 = time.monotonic()
        assert call("POST", "/api/session/stop").json() == {"state": "stopping"}
        assert time.monotonic() - t0 < 1.0
        assert wait(lambda: call("GET", "/api/session").json()["state"] == "ended")
        procs = call("GET", "/api/session").json()["processes"]
        assert procs["engine"]["state"] == "stopped" and procs["chat"]["state"] == "stopped"
        report = call("GET", f"/api/report/{sid}").json()
        assert report["ended_at"] and report["totals"]["events"] == 1
        assert (
            report["timeline"][0]["category"] == "spoken_phone"
            and report["timeline"][0]["confidence"] == 0.91
        )
        # pairing is gone with the session
        assert call("GET", "/api/pairing").status_code == 409

        # a new session can start after the old one ended
        assert call("POST", "/api/session/start", json={"destinations": [YT]}).status_code == 200
        call("POST", "/api/session/stop")
        assert wait(lambda: call("GET", "/api/session").json()["state"] == "ended")
    finally:
        c.__exit__(None, None, None)

    password = app.state.sessions.record.ingest_login[1] if app.state.sessions.record else ""
    everything = "\n".join(bodies) + caplog.text
    assert KEY_TYPED not in everything and FAKE_YT_KEY not in everything and FAKE_TW_KEY not in everything
    # the report and health never carry the publish login or tokens
    assert all(password not in b for b in bodies if '"timeline"' in b or '"ready"' in b)
    assert app.state.tokens.issue("streamer") not in caplog.text
    # no child is left behind after the app shut down
    assert app.state.orchestrator.engine.state == "stopped"


def test_engine_that_cannot_start_is_a_503_and_leaves_the_api_idle(spawning):
    app = spawning(
        Config(_env_file=None, youtube_stream_key=FAKE_YT_KEY), session_engine_cmd="/nonexistent/engine"
    )  # type: ignore[arg-type]
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        r = c.post("/api/session/start", json={"destinations": [YT]})
        assert r.status_code == 503 and r.json()["error"] == "engine_unavailable"
        assert FAKE_YT_KEY not in r.text and "nonexistent" not in r.text
        assert c.get("/api/session").json()["state"] == "idle"
        assert app.state.recorder.current is None


def test_engine_crash_marks_the_session_and_a_new_start_is_allowed(spawning):
    cfg = Config(_env_file=None, youtube_stream_key=FAKE_YT_KEY)  # type: ignore[arg-type]
    app = spawning(cfg, session_engine_cmd=f'{PY} -c "import sys; sys.exit(3)"')
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        assert c.post("/api/session/start", json={"destinations": [YT]}).status_code == 200
        assert wait(lambda: c.get("/api/session").json()["state"] == "failed")
        assert c.get("/api/session").json()["processes"]["engine"]["exit_code"] == 3
        app.state.orchestrator._engine_cmd = SLEEP_CMD.split(" ", 1)[:1] + [
            "-c",
            "import time; time.sleep(60)",
        ]
        assert (
            c.post("/api/session/start", json={"destinations": [YT]}).status_code == 200
        )  # old record closed
        assert c.post("/api/session/stop").status_code == 200
        assert wait(lambda: c.get("/api/session").json()["state"] == "ended")


def test_attach_mode_uses_an_engine_started_by_hand(settings_factory, bus, publish, sample, config_with_keys):
    """session_spawn=false (or an engine already heartbeating): nothing is spawned, stop still builds the report."""
    app = create_app(settings_factory(session_stop_timeout_s=2.0), bus, config_with_keys)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state="ready"))
        assert wait(lambda: c.get("/api/session/health").json()["engine"]["heartbeat"])
        r = c.post("/api/session/start", json={"destinations": [YT]})
        assert r.status_code == 200
        publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state="waiting_ingest"))
        assert wait(lambda: c.get("/api/session").json()["state"] == "waiting_ingest")
        assert c.get("/api/session").json()["processes"] == {
            "enabled": False, "attached": False, "engine": None, "chat": None,
        }  # fmt: skip
        assert c.get("/api/session/health").json()["ready"] is True
        publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state="ready"))
        assert c.post("/api/session/stop").json() == {"state": "stopping"}
        assert wait(lambda: c.get("/api/session").json()["state"] == "ended")
        assert c.get(f"/api/report/{r.json()['session_id']}").status_code == 200


def test_pairing_uses_the_injected_builder_and_hides_nothing_extra(settings_factory, bus):
    class Fake:
        rtmp_url = "rtmp://127.0.0.1:1935/camera?user=u&pass=p"
        srt_url = "srt://127.0.0.1:8890?streamid=publish:camera:u:p"

        def moblin_url(self, protocol: str = "srt") -> str:
            return f"moblin://?fake-{protocol}"

    app = create_app(settings_factory(), bus, Config(_env_file=None, youtube_stream_key=FAKE_YT_KEY))  # type: ignore[arg-type]
    seen: list[tuple[str, str, str]] = []
    app.state.pairing_builder = lambda host, user, pw: (seen.append((host, user, pw)), Fake())[1]
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        assert c.post("/api/session/start", json={"destinations": [YT]}).status_code == 200
        body = c.get("/api/pairing").json()
        assert body == {
            "rtmp": {"url": Fake.rtmp_url}, "srt": {"url": Fake.srt_url},
            "moblin_url": "moblin://?fake-srt", "moblin_rtmp_url": "moblin://?fake-rtmp",
        }  # fmt: skip
        assert seen[0][0] == "127.0.0.1" and seen[0][1].startswith("ss")
        assert FAKE_YT_KEY not in c.get("/api/pairing").text


def test_wildcard_ingest_host_is_refused_for_pairing(settings_factory, bus):
    app = create_app(
        settings_factory(ingest_host="0.0.0.0"), bus, Config(_env_file=None, youtube_stream_key=FAKE_YT_KEY)
    )  # type: ignore[arg-type]  # noqa: S104
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        assert c.post("/api/session/start", json={"destinations": [YT]}).status_code == 200
        r = c.get("/api/pairing")
        assert r.status_code == 422 and r.json()["error"] == "pairing_unavailable"


def test_destination_routes_are_streamer_only_and_keys_are_clearable(spawning, role_headers=None):
    app = spawning()
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/api/destinations").status_code == 401
        for role in ("moderator", "hotkey", "viewer-readonly"):
            h = {"Authorization": f"Bearer {app.state.tokens.issue(role)}"}
            assert (
                c.put("/api/destinations/twitch/key", json={"key": KEY_TYPED}, headers=h).status_code == 403
            )
        h = {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
        assert c.put("/api/destinations/twitch/key", json={"key": KEY_TYPED}, headers=h).status_code == 200
        assert c.delete("/api/destinations/twitch/key", headers=h).json()["cleared"] is True
        assert c.delete("/api/destinations/twitch/key", headers=h).json()["cleared"] is False
        assert c.put("/api/destinations/vimeo/key", json={"key": KEY_TYPED}, headers=h).status_code == 422
        assert "DestinationKeys(configured=[])" == repr(app.state.keys)
