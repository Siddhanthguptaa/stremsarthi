"""Glue fixed by the first real end-to-end smoke (scripts/e2e_smoke.sh): rehearsal start, ingest ports, empty keys."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.main import create_app
from app.api.orchestrator import ingest_urls
from app.api.pairing import build_pairing_info
from contracts import Config, Topic


def _client(settings_factory, bus, config: Config | None = None):
    app = create_app(settings_factory(), bus, config)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        yield c, app


def test_rehearsal_start_needs_no_destination_and_no_key(settings_factory, bus):
    for c, app in _client(settings_factory, bus):
        r = c.post("/api/session/start", json={"destinations": [], "rehearsal": True, "delay_seconds": 4.0})
        assert r.status_code == 200
        dests = {d.kind: d.enabled for d in app.state.config_store.config.destinations}
        assert dests["youtube"] is False and dests["twitch"] is False and dests["mediamtx_tap"] is True
        assert [e.action for t, e in bus.bus.history if t is Topic.SESSION] == ["start"]


def test_start_without_destination_is_still_422_unless_rehearsal(settings_factory, bus):
    for c, _ in _client(settings_factory, bus):
        r = c.post("/api/session/start", json={"destinations": []})
        assert r.status_code == 422 and r.json()["error"] == "no_destination"


def test_empty_stream_key_from_dotenv_is_not_a_configured_key(settings_factory, bus):
    cfg = Config(_env_file=None, youtube_stream_key=SecretStr(""), twitch_stream_key=SecretStr("  "))  # type: ignore[arg-type]
    for c, _ in _client(settings_factory, bus, cfg):
        r = c.post("/api/session/start", json={"destinations": [{"kind": "youtube", "enabled": True}]})
        assert r.status_code == 422 and r.json()["error"] == "missing_keys"
        assert c.get("/api/config").json()["secrets"]["youtube_stream_key"] is None


def test_ingest_urls_and_pairing_use_the_configured_ports():
    urls = ingest_urls("127.0.0.1", "u1", "p1", 21935, 28890)
    assert ":21935/camera" in urls["rtmp_url"] and ":28890?" in urls["srt_url"]
    info = build_pairing_info("127.0.0.1", "u1", "p1", None, 21935, 28890)
    assert ":21935/camera" in info.rtmp_url and ":28890?" in info.srt_url


def test_session_ingest_urls_carry_the_configured_ports(settings_factory, bus):
    cfg = Config(_env_file=None, ingest_port_rtmp=21935, ingest_port_srt=28890)
    for c, _ in _client(settings_factory, bus, cfg):
        c.post("/api/session/start", json={"destinations": [], "rehearsal": True})
        ing = c.get("/api/session").json()["ingest"]
        assert ":21935/camera" in ing["rtmp_url"] and ":28890?" in ing["srt_url"]
        p = c.get("/api/pairing").json()
        assert ":21935/camera" in p["rtmp"]["url"] and ":28890" in p["srt"]["url"]
