"""Demo preview: token scope, privacy gates of the raw stream, ffmpeg lifecycle (fake ffmpeg, no real media)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.demo_preview import PreviewError, PreviewHub, SessionView
from app.api.main import create_app

FAKE = str(Path(__file__).with_name("fake_ffmpeg.py"))
LOOPBACK = ("127.0.0.1", 50000)


class Holder:
    """Mutable session view the hub reads, so a test can move the session between states."""

    def __init__(self, state: str = "live") -> None:
        self.state = state

    def __call__(self) -> SessionView:
        return SessionView(state=self.state)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_gone(pid: int, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ffmpeg_log(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "ffmpeg.json"
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(path))
    return path


def make_app(settings_factory, bus, state="live", **settings):
    app = create_app(
        settings_factory(demo_ffmpeg_cmd=shlex.join([sys.executable, FAKE]), **settings),
        bus,
    )
    holder = Holder(state)
    app.state.demo_preview.view_fn = holder
    return app, holder


def tclient(app, host=LOOPBACK):
    return TestClient(app, base_url="http://127.0.0.1:8000", client=host)


def recorded(path: Path) -> dict:
    for _ in range(100):
        if path.exists() and path.read_text(encoding="utf-8"):
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    raise AssertionError("fake ffmpeg never started")


def preview_token(c: TestClient, app) -> str:
    return c.post(
        "/api/demo/preview-token", headers={"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
    ).json()["token"]


# --- token scope ---------------------------------------------------------------------------------


def test_preview_token_is_streamer_only_and_300_s(settings_factory, bus):
    app, _ = make_app(settings_factory, bus)
    with tclient(app) as c:
        assert c.post("/api/demo/preview-token").status_code == 401
        for role in ("moderator", "hotkey", "viewer-readonly"):
            h = {"Authorization": f"Bearer {app.state.tokens.issue(role)}"}
            assert c.post("/api/demo/preview-token", headers=h).status_code == 403
        body = c.post(
            "/api/demo/preview-token",
            headers={"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"},
        ).json()
        assert body["expires_in_s"] == 300 and body["token"].count(".") == 1
        assert "token" in body and set(body) == {"token", "expires_in_s"}


def test_preview_token_authorises_nothing_else(settings_factory, bus):
    app, _ = make_app(settings_factory, bus)
    with tclient(app) as c:
        token = preview_token(c, app)
        h = {"Authorization": f"Bearer {token}"}
        for method, path in (
            ("GET", "/api/status"),
            ("GET", "/api/session"),
            ("GET", "/api/demo/status"),
            ("POST", "/api/hold"),
            ("POST", "/api/demo/preview-token"),
            ("GET", "/api/report"),
        ):
            assert (
                c.request(method, path, headers=h, json={} if method == "POST" else None).status_code == 401
            )
        # and the other way round: a streamer token is not a preview token
        streamer = app.state.tokens.issue("streamer")
        assert c.get(f"/api/demo/preview/protected.mjpg?t={streamer}").status_code == 401


def test_stream_needs_a_valid_unexpired_token_of_this_instance(settings_factory, bus):
    app, _ = make_app(settings_factory, bus)
    other, _ = make_app(settings_factory, bus)
    with tclient(app) as c:
        assert c.get("/api/demo/preview/protected.mjpg").status_code == 401
        assert c.get("/api/demo/preview/protected.mjpg?t=garbage").status_code == 401
        expired = app.state.tokens.issue_preview(-5)
        assert c.get(f"/api/demo/preview/protected.mjpg?t={expired}").status_code == 401
        foreign = other.state.tokens.issue_preview()
        assert c.get(f"/api/demo/preview/protected.mjpg?t={foreign}").status_code == 401
        assert app.state.tokens.verify_preview(app.state.tokens.issue_preview())
        assert app.state.tokens.verify(app.state.tokens.issue_preview()) is None


def test_auth_off_still_requires_a_preview_token(settings_factory, bus):
    app, _ = make_app(settings_factory, bus, require_auth=False)
    with tclient(app) as c:
        assert c.get("/api/demo/preview/protected.mjpg").status_code == 401


# --- protected stream ----------------------------------------------------------------------------


def test_protected_stream_is_multipart_without_caching_and_ffmpeg_is_clean(
    settings_factory, bus, ffmpeg_log, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "finite")
    app, _ = make_app(settings_factory, bus)
    with tclient(app) as c:
        r = c.get(f"/api/demo/preview/protected.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
        assert "no-store" in r.headers["cache-control"] and "no-cache" in r.headers["cache-control"]
        assert r.headers["pragma"] == "no-cache" and r.headers["expires"] == "0"
        assert r.content.count(b"--frame\r\n") >= 1  # frames the client could keep up with (latest wins)
        assert b"Content-Type: image/jpeg" in r.content and b"FRAME" in r.content
        info = recorded(ffmpeg_log)
        argv = info["argv"]
        assert "-an" in argv and "image2pipe" in argv and "mjpeg" in argv and "pipe:1" in argv
        vf = argv[argv.index("-vf") + 1]
        assert vf.startswith("fps=8,") and "scale=" in vf and "640" in vf
        assert argv[argv.index("-i") + 1] == "rtmp://127.0.0.1:1935/shielded"  # anonymous loopback path
        assert "camera" not in " ".join(argv) and "pass=" not in " ".join(argv)
        assert info["stdin"] == ""
        assert wait_gone(info["pid"])
        assert app.state.demo_preview.clients() == {"raw": 0, "protected": 0}


def test_no_session_and_not_live_and_dead_ffmpeg_codes(settings_factory, bus, ffmpeg_log, monkeypatch):
    app, holder = make_app(settings_factory, bus, state="idle")
    with tclient(app) as c:
        url = f"/api/demo/preview/protected.mjpg?t={preview_token(c, app)}"
        for state in ("idle", "ended", "failed", "stopping", "engine_lost"):
            holder.state = state
            r = c.get(url)
            assert r.status_code == 409 and r.json()["error"] == "no_session", state
        for state in ("starting", "waiting_ingest"):
            holder.state = state
            r = c.get(url)
            assert r.status_code == 503 and r.json()["error"] == "stream_unavailable", state
        holder.state = "live"
        monkeypatch.setenv("FAKE_FFMPEG_MODE", "die")
        r = c.get(url)
        assert r.status_code == 503 and r.json()["error"] == "stream_unavailable"
        assert app.state.demo_preview.clients()["protected"] == 0


def test_silent_ffmpeg_times_out_and_is_killed(settings_factory, bus, ffmpeg_log, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "silent")
    app, _ = make_app(settings_factory, bus, demo_first_frame_timeout_s=2.0)
    with tclient(app) as c:
        r = c.get(f"/api/demo/preview/protected.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 503 and r.json()["error"] == "stream_unavailable"
        assert wait_gone(recorded(ffmpeg_log)["pid"])


def test_missing_ffmpeg_binary_is_503(settings_factory, bus):
    app = create_app(settings_factory(demo_ffmpeg_cmd="/nonexistent/ffmpeg-nope"), bus)
    app.state.demo_preview.view_fn = Holder()
    with tclient(app) as c:
        r = c.get(f"/api/demo/preview/protected.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 503 and r.json()["error"] == "stream_unavailable"


# --- raw stream: privacy gates -------------------------------------------------------------------


def test_raw_is_off_by_default(settings_factory, bus, ffmpeg_log):
    app, _ = make_app(settings_factory, bus)
    with tclient(app) as c:
        r = c.get(f"/api/demo/preview/raw.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 403 and r.json()["error"] == "raw_preview_disabled"
        assert not ffmpeg_log.exists()  # nothing was even started


def test_raw_refused_for_a_non_loopback_client(settings_factory, bus, ffmpeg_log):
    app, _ = make_app(settings_factory, bus, demo_raw_preview=True)
    with tclient(app, host=("192.168.1.20", 5555)) as c:
        r = c.get(f"/api/demo/preview/raw.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 403 and r.json()["error"] == "raw_preview_loopback_only"
        assert not ffmpeg_log.exists()


def test_raw_refused_when_the_api_is_on_the_lan(settings_factory, bus, ffmpeg_log):
    app, _ = make_app(settings_factory, bus, demo_raw_preview=True, lan_host="192.168.1.5")
    with tclient(app) as c:  # even a loopback client: the API itself is exposed
        r = c.get(f"/api/demo/preview/raw.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 403 and r.json()["error"] == "raw_preview_loopback_only"
        assert (
            c.get(
                "/api/demo/status", headers={"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
            ).json()["raw_enabled"]
            is False
        )
        assert not ffmpeg_log.exists()


def test_raw_needs_a_valid_token_before_anything_else(settings_factory, bus, ffmpeg_log):
    app, _ = make_app(settings_factory, bus, demo_raw_preview=True)
    with tclient(app) as c:
        assert c.get("/api/demo/preview/raw.mjpg").status_code == 401
        assert not ffmpeg_log.exists()


def test_raw_serves_loopback_anonymously_and_logs_nothing_sensitive(
    settings_factory, bus, ffmpeg_log, monkeypatch, caplog
):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "finite")
    caplog.set_level(logging.DEBUG)
    app, _ = make_app(settings_factory, bus, demo_raw_preview=True)
    with tclient(app) as c:
        token = preview_token(c, app)
        r = c.get(f"/api/demo/preview/raw.mjpg?t={token}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
        info = recorded(ffmpeg_log)
        argv_text = " ".join(info["argv"])
        argv = info["argv"]
        # anonymous loopback read: no login anywhere, nothing on stdin
        assert argv[argv.index("-i") + 1] == "rtmp://127.0.0.1:1935/camera"
        assert "pass=" not in argv_text and "user=" not in argv_text and info["stdin"] == ""
        assert wait_gone(info["pid"])
    # the test client's own httpx/asyncio loggers echo the request URL: only the server side counts
    server_records = [
        rec for rec in caplog.records if not rec.name.startswith(("httpx", "asyncio", "httpcore"))
    ]
    logs = " ".join(rec.getMessage() for rec in server_records)
    assert token not in logs and "rtmp://" not in logs
    assert b"camera" not in r.content and b"rtmp" not in r.content


def test_nothing_is_written_to_disk(settings_factory, bus, ffmpeg_log, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_MODE", "finite")
    monkeypatch.chdir(tmp_path)
    app, _ = make_app(settings_factory, bus, demo_raw_preview=True)
    before = {p.name for p in tmp_path.iterdir()}
    with tclient(app) as c:
        for stream in ("raw", "protected"):
            assert c.get(f"/api/demo/preview/{stream}.mjpg?t={preview_token(c, app)}").status_code == 200
    after = {p.name for p in tmp_path.iterdir()} - {"ffmpeg.json"}  # the fake's own test log
    assert after == before


# --- status --------------------------------------------------------------------------------------


def test_status(settings_factory, bus):
    app, holder = make_app(settings_factory, bus, demo_raw_preview=True)
    with tclient(app) as c:
        h = {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
        assert c.get("/api/demo/status").status_code == 401
        assert (
            c.get(
                "/api/demo/status", headers={"Authorization": f"Bearer {app.state.tokens.issue('moderator')}"}
            ).status_code
            == 403
        )
        assert c.get("/api/demo/status", headers=h).json() == {
            "raw_enabled": True,
            "protected_available": True,
            "raw_available": True,
            "clients": {"raw": 0, "protected": 0},
            "max_clients": 4,
        }
        holder.state = "waiting_ingest"
        s = c.get("/api/demo/status", headers=h).json()
        assert s["protected_available"] is False and s["raw_available"] is False and s["raw_enabled"] is True
    off, _ = make_app(settings_factory, bus)
    with tclient(off) as c:
        s = c.get(
            "/api/demo/status", headers={"Authorization": f"Bearer {off.state.tokens.issue('streamer')}"}
        ).json()
        assert s["raw_enabled"] is False and s["raw_available"] is False and s["protected_available"] is True


# --- lifecycle (hub level, real subprocess, fake ffmpeg) -----------------------------------------


def hub_for(holder: Holder, **kw) -> PreviewHub:
    return PreviewHub(holder, [sys.executable, FAKE], **kw)


def test_one_ffmpeg_shared_cap_enforced_and_killed_with_the_last(ffmpeg_log):
    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5, max_clients=2)
        a = await hub.acquire("protected", "10.0.0.1")
        b = await hub.acquire("protected", "10.0.0.2")
        pid = recorded(ffmpeg_log)["pid"]
        assert hub.clients()["protected"] == 2
        with pytest.raises(PreviewError) as exc:
            await hub.acquire("protected", "10.0.0.3")  # a stranger over the cap
        assert exc.value.status == 503 and exc.value.code == "preview_clients_full"
        assert exc.value.headers == {"Retry-After": "2"}
        assert hub.clients()["protected"] == 2  # the refused client did not take a slot
        a.release()
        a.release()  # idempotent: a double release must not free b's slot
        assert hub.clients()["protected"] == 1 and alive(pid)
        b.release()
        assert hub.clients()["protected"] == 0
        await asyncio.sleep(0.3)
        assert wait_gone(pid)

    asyncio.run(scenario())


def test_a_holder_may_overshoot_the_cap_by_one_for_a_token_refresh(ffmpeg_log):
    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5, max_clients=2)
        a1 = await hub.acquire("protected", "10.0.0.1")
        b = await hub.acquire("protected", "10.0.0.2")
        a2 = await hub.acquire("protected", "10.0.0.1")  # new stream opened before the old one closed
        assert hub.clients()["protected"] == 3
        with pytest.raises(PreviewError) as exc:  # but only one overshoot per address
            await hub.acquire("protected", "10.0.0.1")
        assert exc.value.code == "preview_clients_full"
        a1.release()  # the old stream closes: back to the cap
        assert hub.clients()["protected"] == 2
        for lease in (a2, b):
            lease.release()

    asyncio.run(scenario())


def test_a_stalled_client_frees_its_slot_without_any_disconnect(ffmpeg_log):
    """uvicorn may not deliver a disconnect: a lease that stops pulling frames is evicted after ``stall_s``."""

    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5, max_clients=1, stall_s=0.6)
        stuck = await hub.acquire("protected", "10.0.0.1")
        gen = hub.frames(stuck)
        await gen.__anext__()  # the client took one part and then its connection went dead: no more reads
        pid = recorded(ffmpeg_log)["pid"]
        assert hub.clients()["protected"] == 1
        t0 = time.monotonic()
        while hub.clients()["protected"] and time.monotonic() - t0 < 3:  # noqa: ASYNC110
            await asyncio.sleep(0.05)
        assert hub.clients()["protected"] == 0 and time.monotonic() - t0 < 2.0
        assert wait_gone(pid)  # last slot gone: ffmpeg is killed too
        fresh = await hub.acquire("protected", "10.0.0.9")  # and a new viewer is not locked out
        fresh.release()
        await gen.aclose()

    asyncio.run(scenario())


def test_stalled_slot_is_evicted_when_a_new_client_arrives(ffmpeg_log):
    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5, max_clients=1, stall_s=0.3)
        stuck = await hub.acquire("protected", "10.0.0.1")
        await asyncio.sleep(0.5)
        fresh = await hub.acquire("protected", "10.0.0.2")  # no preview_clients_full: the old one is dead
        assert stuck.done and hub.clients()["protected"] == 1
        fresh.release()

    asyncio.run(scenario())


def test_idle_stream_sends_keepalive_parts(ffmpeg_log, monkeypatch):
    """No new frame for a second: the last frame is written again so a dead socket gets noticed."""

    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5)
        lease = await hub.acquire("protected", "10.0.0.1")
        run = lease.run
        gen = hub.frames(lease)
        await gen.__anext__()

        async def no_more_frames(_jpeg: bytes) -> None:  # freeze the producer
            return None

        run.publish = no_more_frames  # type: ignore[method-assign]
        t0 = time.monotonic()
        part = await asyncio.wait_for(gen.__anext__(), 3)
        part2 = await asyncio.wait_for(gen.__anext__(), 3)
        assert part.startswith(b"--frame") and part2.startswith(b"--frame")
        assert time.monotonic() - t0 < 3.5
        await gen.aclose()

    asyncio.run(scenario())


def test_streams_are_independent_processes(ffmpeg_log, tmp_path):
    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5)
        p = await hub.acquire("protected")
        pid_p = recorded(ffmpeg_log)["pid"]
        ffmpeg_log.unlink()
        r = await hub.acquire("raw")
        pid_r = recorded(ffmpeg_log)["pid"]
        assert pid_p != pid_r and hub.clients() == {"raw": 1, "protected": 1}
        r.release()
        await asyncio.sleep(0.2)
        assert wait_gone(pid_r) and alive(pid_p)
        p.release()
        assert wait_gone(pid_p)

    asyncio.run(scenario())


def test_frames_generator_stops_when_the_session_ends_and_kills_ffmpeg(ffmpeg_log):
    async def scenario() -> None:
        holder = Holder()
        hub = hub_for(holder, first_frame_timeout_s=5)
        lease = await hub.acquire("protected")
        pid = recorded(ffmpeg_log)["pid"]
        parts = []
        async for part in hub.frames(lease):
            parts.append(part)
            if len(parts) == 3:
                holder.state = "ended"  # the session ends while a client is watching
        assert len(parts) >= 3 and all(p.startswith(b"--frame\r\n") for p in parts)
        assert hub.clients()["protected"] == 0 and wait_gone(pid)

    asyncio.run(scenario())


def test_closing_the_generator_early_kills_ffmpeg(ffmpeg_log):
    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5)
        lease = await hub.acquire("protected")
        pid = recorded(ffmpeg_log)["pid"]
        gen = hub.frames(lease)
        await gen.__anext__()
        await gen.aclose()  # what a client disconnect does to the response body
        assert hub.clients()["protected"] == 0 and wait_gone(pid)

    asyncio.run(scenario())


def test_shutdown_kills_everything(ffmpeg_log):
    async def scenario() -> None:
        hub = hub_for(Holder(), first_frame_timeout_s=5)
        await hub.acquire("protected")
        pid = recorded(ffmpeg_log)["pid"]
        await hub.shutdown()
        assert wait_gone(pid)

    asyncio.run(scenario())


def test_http_disconnect_kills_ffmpeg(settings_factory, bus, ffmpeg_log):
    """Drive the ASGI app by hand: read one chunk, send ``http.disconnect``, expect no ffmpeg left."""
    app, _ = make_app(settings_factory, bus)

    async def scenario() -> None:
        token = app.state.tokens.issue_preview()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "path": "/api/demo/preview/protected.mjpg",
            "raw_path": b"/api/demo/preview/protected.mjpg",
            "query_string": f"t={token}".encode(),
            "root_path": "",
            "scheme": "http",
            "headers": [(b"host", b"127.0.0.1:8000")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
            "app": app,
        }
        got_body = asyncio.Event()
        status: list[int] = []

        async def receive() -> dict:
            await got_body.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status.append(message["status"])
            elif message["type"] == "http.response.body" and message.get("body"):
                got_body.set()

        await asyncio.wait_for(app(scope, receive, send), 10)
        assert status == [200]
        assert app.state.demo_preview.clients() == {"raw": 0, "protected": 0}
        assert wait_gone(recorded(ffmpeg_log)["pid"])

    asyncio.run(scenario())


# --- the engine environment gets DEMO_RAW_PREVIEW only when it should ----------------------------


def engine_sees(tmp_path, settings_factory, bus, monkeypatch, **settings) -> str:
    """Start a rehearsal session whose 'engine' writes its DEMO_RAW_PREVIEW value to a file."""
    from app.api.tests.test_api_golive_flow import open_client, wait

    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "engine_env.txt"
    code = (
        "import os,time;"
        f"open({str(out)!r},'w').write(os.environ.get('DEMO_RAW_PREVIEW','unset'));"
        "time.sleep(60)"
    )
    app = create_app(
        settings_factory(
            session_spawn=True,
            session_engine_cmd=shlex.join([sys.executable, "-c", code]),
            session_stop_timeout_s=3.0,
            sessions_dir=str(tmp_path / "sessions"),
            **settings,
        ),
        bus,
    )
    c = open_client(app)
    try:
        assert c.post("/api/session/start", json={"destinations": [], "rehearsal": True}).status_code == 200
        assert wait(lambda: out.exists() and out.read_text(encoding="utf-8") != "")
        return out.read_text(encoding="utf-8")
    finally:
        c.post("/api/session/stop")
        c.__exit__(None, None, None)


def test_engine_gets_raw_preview_env_only_when_on_and_loopback(settings_factory, bus, tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_RAW_PREVIEW", raising=False)
    assert engine_sees(tmp_path / "a", settings_factory, bus, monkeypatch, demo_raw_preview=True) == "1"


def test_engine_never_inherits_raw_preview_from_the_api_environment(
    settings_factory, bus, tmp_path, monkeypatch
):
    monkeypatch.setenv("DEMO_RAW_PREVIEW", "1")  # set by accident in the API's own environment
    assert engine_sees(tmp_path / "b", settings_factory, bus, monkeypatch, demo_raw_preview=False) == "unset"


def test_engine_gets_no_raw_preview_env_on_lan(settings_factory, bus, tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_RAW_PREVIEW", "1")
    seen = engine_sees(
        tmp_path / "c", settings_factory, bus, monkeypatch, demo_raw_preview=True, lan_host="192.168.1.5"
    )
    assert seen == "unset"


# --- configurable cap, distinct error, status --------------------------------------------------------


def test_cap_setting_default_and_bounds(settings_factory):
    assert settings_factory().demo_max_clients == 4
    assert settings_factory(demo_max_clients=8).demo_max_clients == 8
    for bad in (0, 9):
        with pytest.raises(ValueError):
            settings_factory(demo_max_clients=bad)


def test_full_cap_is_503_preview_clients_full_with_retry_after_and_status_shows_it(
    settings_factory, bus, ffmpeg_log
):
    app, _ = make_app(settings_factory, bus, demo_max_clients=2)
    hub = app.state.demo_preview
    with tclient(app) as c:
        h = {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}
        leases = [c.portal.call(hub.acquire, "protected", f"9.9.9.{i}") for i in (1, 2)]
        r = c.get(f"/api/demo/preview/protected.mjpg?t={preview_token(c, app)}")
        assert r.status_code == 503
        assert r.json() == {"error": "preview_clients_full", "detail": "at most 2 viewers per stream"}
        assert r.headers["retry-after"] == "2"
        status = c.get("/api/demo/status", headers=h).json()
        assert status["clients"] == {"raw": 0, "protected": 2} and status["max_clients"] == 2
        # other errors keep their own codes and carry no Retry-After
        hub_state = hub.view_fn
        hub.view_fn = lambda: SessionView(state="idle")
        r = c.get(f"/api/demo/preview/protected.mjpg?t={preview_token(c, app)}")
        assert r.json()["error"] == "no_session" and "retry-after" not in r.headers
        hub.view_fn = hub_state
        for lease in leases:
            c.portal.call(lease.release)  # the hub lives in the app's loop thread
        assert c.get("/api/demo/status", headers=h).json()["clients"]["protected"] == 0


def test_two_tabs_and_a_refresh_fit_in_the_default_cap(settings_factory, bus, ffmpeg_log):
    """The first real browser run: two tabs (raw + protected each) and a remount must not hit the cap."""
    app, _ = make_app(settings_factory, bus, demo_raw_preview=True)
    hub = app.state.demo_preview
    with tclient(app) as c:
        leases = [
            c.portal.call(hub.acquire, stream, "127.0.0.1")
            for stream in ("protected", "raw")
            for _ in range(3)  # two tabs and a remount that opened before the old pane closed
        ]
        assert hub.clients() == {"raw": 3, "protected": 3}
        for lease in leases:
            c.portal.call(lease.release)
