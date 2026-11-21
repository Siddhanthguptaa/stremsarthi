"""Config set before the engine exists reaches it: republish after start-up, restart-only keys as env, mute mode,
the attach-mode delay rule and the ``applied`` / ``engine_ack`` fields."""

from __future__ import annotations

import shlex
import sys
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app.api.config_store import ConfigStore
from app.api.config_sync import ConfigSync, changes_for
from app.api.main import create_app
from app.api.tests.conftest import FAKE_GROQ_KEY, FAKE_TW_KEY, FAKE_YT_KEY
from contracts import Config, ConfigChange, Topic
from contracts.samples import samples


def config_changes(bus) -> list[ConfigChange]:
    return [e for t, e in bus.bus.history if t is Topic.CONFIG]


def sent_diffs(bus) -> list[dict]:
    return [dict(c.diff) for c in config_changes(bus)]


class Probe:
    """Drives ``ConfigSync.poll`` with scripted engine, session and chat state."""

    def __init__(self, store: ConfigStore, bus) -> None:
        self.state: str | None = None
        self.sid: str | None = None
        self.chat = False
        self.sync = ConfigSync(
            store, bus, lambda: self.state, lambda: self.sid, lambda: self.chat, offsets_s=(0.0, 2.0, 5.0)
        )


def store_with_toggle() -> ConfigStore:
    store = ConfigStore(Config(_env_file=None))
    new_config, diff = store.plan_patch(
        {
            "categories": {"face": {"enabled": False}},
            "captions": {"enabled": True},
        },
        live=False,
    )
    assert diff == {"categories.face.enabled": False, "captions.enabled": True}
    store.commit(new_config, diff)
    return store


# --- A: republish ---------------------------------------------------------------------------------


def test_overrides_are_republished_in_a_burst_when_the_engine_comes_up(bus):
    probe = Probe(store_with_toggle(), bus)
    probe.sync.poll(100.0)  # no engine yet: nothing is sent
    assert sent_diffs(bus) == []
    probe.state = "starting"
    probe.sync.poll(101.0)  # first heartbeat: burst starts at once
    assert sent_diffs(bus) == [{"categories.face.enabled": False, "captions.enabled": True}]
    probe.state = "ready"
    probe.sync.poll(102.0)  # starting -> ready: the handlers are up, a fresh burst starts
    assert len(sent_diffs(bus)) == 2
    probe.sync.poll(104.1)  # +2 s of that burst
    probe.sync.poll(107.1)  # +5 s
    probe.sync.poll(120.0)  # nothing left
    assert len(sent_diffs(bus)) == 4
    assert all(c.actor == "config-sync" for c in config_changes(bus))


def test_republish_again_when_chat_starts_and_when_the_engine_restarts(bus):
    probe = Probe(store_with_toggle(), bus)
    probe.state = "live"
    probe.sync.poll(10.0)
    probe.sync.poll(12.1)
    probe.sync.poll(15.1)
    base = len(sent_diffs(bus))
    assert base == 3
    probe.chat = True  # the chat process just started: it never saw the toggles
    probe.sync.poll(30.0)
    assert len(sent_diffs(bus)) == base + 1
    probe.sync.poll(40.0)
    probe.sync.poll(41.0)
    before = len(sent_diffs(bus))
    probe.state = None  # heartbeats stopped (engine died)
    probe.sync.poll(50.0)
    probe.state = "starting"  # a restarted engine
    probe.sync.poll(60.0)
    assert len(sent_diffs(bus)) == before + 1


def test_nothing_is_sent_without_overrides(bus):
    probe = Probe(ConfigStore(Config(_env_file=None)), bus)
    probe.state = "live"
    for t in (1.0, 3.0, 6.0, 20.0):
        probe.sync.poll(t)
    assert config_changes(bus) == []


def test_changes_are_chunked_and_a_bad_value_does_not_drop_the_rest():
    many = {f"categories.c{i}.sensitivity": 0.5 for i in range(60)}
    packed = changes_for(many)
    assert len(packed) == 3 and sum(len(c.diff) for c in packed) == 60
    mixed = {"captions.enabled": True, "captions.bad": {"nested": 1}}
    assert [dict(c.diff) for c in changes_for(mixed)] == [{"captions.enabled": True}]


def test_restart_only_keys_are_never_in_the_live_republish_but_go_to_the_environment():
    store = ConfigStore(Config(_env_file=None))
    new_config, diff = store.plan_patch({"detect": {"stride": 2}, "gpu": {"enabled": True}}, live=False)
    store.commit(new_config, diff)
    assert store.live_overrides() == {}
    assert store.restart_env() == {"DETECT__STRIDE": "2", "GPU__ENABLED": "true"}


def test_a_patched_toggle_before_start_reaches_the_engine_and_secrets_never_do(settings_factory, bus):
    cfg = Config(
        _env_file=None,
        youtube_stream_key=FAKE_YT_KEY,  # type: ignore[arg-type]
        twitch_stream_key=FAKE_TW_KEY,  # type: ignore[arg-type]
        groq_api_key=FAKE_GROQ_KEY,  # type: ignore[arg-type]
    )
    app = create_app(settings_factory(), bus, cfg)
    app.state.config_sync.offsets_s = (0.0, 0.2)
    app.state.config_sync.tick_s = 0.05
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        h = {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
        assert (
            c.patch("/api/config", json={"categories": {"face": {"enabled": False}}}, headers=h).status_code
            == 200
        )
        before = len(sent_diffs(bus))  # the live publish from the PATCH itself (to nobody)
        sample_status = next(s for s in samples() if type(s).__name__ == "EngineStatus")
        c.portal.call(bus.publish, Topic.ENGINE_STATUS, replace(sample_status, state="starting"))
        deadline = time.monotonic() + 3
        while len(sent_diffs(bus)) < before + 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        resent = sent_diffs(bus)[before:]
        assert {"categories.face.enabled": False} in resent
    published = str(sent_diffs(bus))
    for secret in (FAKE_YT_KEY, FAKE_TW_KEY, FAKE_GROQ_KEY):
        assert secret not in published


def test_restart_only_override_is_passed_to_the_spawned_engine_in_its_environment(
    settings_factory, bus, tmp_path
):
    from app.api.tests.test_api_golive_flow import open_client, wait

    out = tmp_path / "env.txt"
    code = (
        "import os,time;"
        f"open({str(out)!r},'w').write(os.environ.get('DETECT__STRIDE','unset')+','+os.environ.get('GPU__ENABLED','unset'));"
        "time.sleep(60)"
    )
    cmd = shlex.join([sys.executable, "-c", code])
    app = create_app(
        settings_factory(
            session_spawn=True,
            session_engine_cmd=cmd,
            session_stop_timeout_s=3.0,
            sessions_dir=str(tmp_path / "s"),
        ),
        bus,
    )
    c = open_client(app)
    try:
        assert c.patch("/api/config", json={"detect": {"stride": 2}}).status_code == 200
        assert c.post("/api/session/start", json={"destinations": [], "rehearsal": True}).status_code == 200
        assert wait(lambda: out.exists() and out.read_text(encoding="utf-8") != "")
        assert out.read_text(encoding="utf-8") == "2,unset"
    finally:
        c.post("/api/session/stop")
        c.__exit__(None, None, None)


# --- B: mute mode ---------------------------------------------------------------------------------


def test_audio_mute_mode_is_a_live_key(client: TestClient, auth, bus):
    r = client.patch("/api/config", json={"audio": {"mute_mode": "bleep"}}, headers=auth)
    assert r.status_code == 200 and r.json()["audio"]["mute_mode"] == "bleep"
    assert sent_diffs(bus)[-1] == {"audio.mute_mode": "bleep"}
    bad = client.patch("/api/config", json={"audio": {"mute_mode": "scream"}}, headers=auth)
    assert bad.status_code == 422


# --- C: delay while attached ----------------------------------------------------------------------


def test_a_different_delay_is_refused_while_attached_to_a_running_engine(
    client: TestClient, auth, publish, sample, wait_until, bus
):
    cfg_delay = client.get("/api/config", headers=auth).json()["delay_seconds"]
    other = 3.0 if cfg_delay != 3.0 else 4.0
    publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state="ready"))
    assert wait_until(lambda: client.get("/api/status", headers=auth).json()["engine"] is not None)
    r = client.post(
        "/api/session/start",
        json={"destinations": [], "rehearsal": True, "delay_seconds": other},
        headers=auth,
    )
    assert r.status_code == 409 and r.json()["error"] == "delay_fixed_while_attached"
    assert not any("delay_seconds" in d for d in sent_diffs(bus))
    same = client.post(
        "/api/session/start",
        json={"destinations": [], "rehearsal": True, "delay_seconds": cfg_delay},
        headers=auth,
    )
    assert same.status_code == 200


def test_a_delay_is_accepted_when_no_engine_is_running(client: TestClient, auth):
    cfg_delay = client.get("/api/config", headers=auth).json()["delay_seconds"]
    other = 3.0 if cfg_delay != 3.0 else 4.0
    r = client.post(
        "/api/session/start",
        json={"destinations": [], "rehearsal": True, "delay_seconds": other},
        headers=auth,
    )
    assert r.status_code == 200


# --- D: applied and engine_ack --------------------------------------------------------------------


def test_patch_says_pending_without_an_engine_and_published_with_one(
    client: TestClient, auth, publish, sample, wait_until
):
    first = client.patch("/api/config", json={"captions": {"enabled": True}}, headers=auth).json()
    assert first["applied"] == "pending" and first["config_version"] == 1 and first["engine_ack"] is None
    publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state="live"))
    assert wait_until(lambda: client.get("/api/status", headers=auth).json()["engine"] is not None)
    second = client.patch("/api/config", json={"captions": {"enabled": False}}, headers=auth).json()
    assert second["applied"] == "published" and second["config_version"] == 2
    got = client.get("/api/config", headers=auth).json()
    assert got["config_version"] == 2 and got["engine_ack"] is None and "applied" not in got
