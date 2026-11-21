"""Reports survive an API stop, pairing always shows the current login, rehearsal -> live switch, stable login."""

from __future__ import annotations

import json

from app.api.tests.test_api_golive_flow import (  # noqa: F401  (fixtures and helpers)
    KEY_TYPED,
    YT,
    open_client,
    safety_event,
    spawning,
    wait,
)
from contracts import Config, Topic

REHEARSAL = {"destinations": [], "rehearsal": True}


def _state(c) -> str:
    return c.get("/api/session").json()["state"]


def test_report_is_saved_on_stop_and_served_after_a_restart(spawning, publish, tmp_path):  # noqa: F811
    app = spawning(Config(_env_file=None))
    c = open_client(app)
    try:
        sid = c.post("/api/session/start", json=REHEARSAL).json()["session_id"]
        publish(Topic.SAFETY_EVENT, safety_event())
        assert wait(lambda: len(app.state.recorder.current.events) == 1)
        c.post("/api/session/stop")
        assert wait(lambda: _state(c) == "ended")
    finally:
        c.__exit__(None, None, None)
    path = tmp_path / "sessions" / sid / "report.json"
    assert path.is_file()
    saved = path.read_text(encoding="utf-8")
    assert "data:image" not in saved and KEY_TYPED not in saved

    app2 = spawning(Config(_env_file=None))  # a fresh API process, same sessions dir
    c2 = open_client(app2)
    try:
        assert sid in c2.get("/api/report").json()["sessions"]
        rep = c2.get(f"/api/report/{sid}").json()
        assert rep["ended_at"] and rep["totals"]["events"] == 1
        assert rep["timeline"][0]["category"] == "spoken_phone" and rep["timeline"][0]["confidence"] == 0.91
        assert json.loads(c2.get(f"/api/report/{sid}/export?format=json").text)["session_id"] == sid
        assert "Stream Sarthi" in c2.get(f"/api/report/{sid}/export?format=html").text
        # a hand-edited file that carries a phone number is refused, not served
        bad = json.loads(saved)
        bad["notes"] = ["call 9876543210"]
        path.write_text(json.dumps(bad), encoding="utf-8")
        assert c2.get(f"/api/report/{sid}").status_code == 404
        # erasing the session data removes the saved report too
        path.write_text(saved, encoding="utf-8")
        assert c2.delete(f"/api/session/{sid}/data").json() == {"deleted": True}
        assert c2.get(f"/api/report/{sid}").status_code == 404
    finally:
        c2.__exit__(None, None, None)


def test_api_shutdown_with_a_session_open_still_saves_the_report(spawning, publish, tmp_path):  # noqa: F811
    app = spawning(Config(_env_file=None))
    c = open_client(app)
    sid = c.post("/api/session/start", json=REHEARSAL).json()["session_id"]
    publish(Topic.SAFETY_EVENT, safety_event())
    assert wait(lambda: len(app.state.recorder.current.events) == 1)
    c.__exit__(None, None, None)  # Ctrl-C of the API: the session was never stopped
    assert app.state.orchestrator.engine.state == "stopped"
    saved = json.loads((tmp_path / "sessions" / sid / "report.json").read_text(encoding="utf-8"))
    assert saved["ended_at"] and saved["totals"]["events"] == 1


def test_go_live_while_a_rehearsal_runs_switches_and_pairing_shows_the_new_login(spawning):  # noqa: F811
    app = spawning(Config(_env_file=None))
    c = open_client(app)
    try:
        first = c.post("/api/session/start", json=REHEARSAL).json()
        assert c.get("/api/session").json()["output"] == "rehearsal"
        old_pair = c.get("/api/pairing").json()["rtmp"]["url"]
        assert c.put("/api/destinations/youtube/key", json={"key": KEY_TYPED}).status_code == 409  # active
        assert c.post("/api/session/start", json={"destinations": [YT]}).status_code == 409  # no flag

        # switching needs the key first: stop-less typing is refused while active, so use the config key path
        app.state.keys.set("youtube", KEY_TYPED)
        r = c.post("/api/session/start", json={"destinations": [YT], "replace_rehearsal": True})
        assert r.status_code == 200, r.text
        assert r.json()["session_id"] != first["session_id"] and r.json()["output"] == "live"
        sess = c.get("/api/session").json()
        assert sess["output"] == "live" and sess["destinations"] == ["youtube"]
        assert sess["processes"]["engine"]["state"] == "running"
        pair = c.get("/api/pairing").json()["rtmp"]["url"]
        assert pair == sess["ingest"]["rtmp_url"] and pair != old_pair  # the CURRENT login

        # a session that goes to a platform is never replaced
        r = c.post("/api/session/start", json={"destinations": [YT], "replace_rehearsal": True})
        assert r.status_code == 409 and r.json()["error"] == "session_active"
        c.post("/api/session/stop")
        assert wait(lambda: _state(c) == "ended")
    finally:
        c.__exit__(None, None, None)


def test_configured_publish_login_is_stable_and_bad_ones_fall_back(spawning):  # noqa: F811
    cfg = Config(_env_file=None, ingest_publish_user="obs_user", ingest_publish_password="obs-pass-12345")
    app = spawning(cfg)
    c = open_client(app)
    try:
        r = c.post("/api/session/start", json=REHEARSAL)
        assert r.json()["login_stable"] is True
        url1 = c.get("/api/pairing").json()["rtmp"]["url"]
        assert "user=obs_user" in url1 and "pass=obs-pass-12345" in url1
        assert app.state.orchestrator.engine._env["INGEST_PUBLISH_USER"] == "obs_user"
        c.post("/api/session/stop")
        assert wait(lambda: _state(c) == "ended")
        c.post("/api/session/start", json=REHEARSAL)
        assert c.get("/api/pairing").json()["rtmp"]["url"] == url1
        c.post("/api/session/stop")
        assert wait(lambda: _state(c) == "ended")
    finally:
        c.__exit__(None, None, None)

    weak = spawning(Config(_env_file=None, ingest_publish_user="obs", ingest_publish_password="short"))
    c = open_client(weak)
    try:
        assert c.post("/api/session/start", json=REHEARSAL).json()["login_stable"] is False
        assert "user=obs&" not in c.get("/api/pairing").json()["rtmp"]["url"]
    finally:
        c.__exit__(None, None, None)
