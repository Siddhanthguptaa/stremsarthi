"""Orchestrator: real (harmless) child processes, supervision, restart policy, secrets only in the env."""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from app.api.orchestrator import CHAT_STATUS_CODES, Orchestrator, ingest_urls, new_ingest_login

PY = sys.executable
SLEEP = [PY, "-c", "import time; time.sleep(60)"]
SECRET_VALUE = "yt-FAKE-STREAM-KEY-9f3a"


def make(engine=None, chat=None, **kw) -> Orchestrator:
    return Orchestrator(
        enabled=True,
        engine_cmd=engine or SLEEP,
        chat_cmd=chat or SLEEP,
        stop_timeout_s=5.0,
        restart_backoff_s=0.05,
        base_env={"PATH": "/usr/bin:/bin"},
        **kw,
    )


async def until(pred, wait_s=5.0):
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


async def start(o: Orchestrator, **kw) -> None:
    await o.start_session(
        profile=kw.pop("profile", "dev"),
        session_id="sess-abc123",
        chat_enabled=kw.pop("chat_enabled", False),
        secret_env=kw.pop("secret_env", {}),
        ingest_env=kw.pop("ingest_env", {}),
        **kw,
    )


def test_engine_starts_and_stops_gracefully():
    async def go():
        o = make()
        await start(o)
        assert o.engine_running() and o.chat is None
        t0 = time.monotonic()
        await o.stop_all()
        assert time.monotonic() - t0 < 3.0
        assert o.engine.state == "stopped" and o.engine.exit_code is not None

    asyncio.run(go())


def test_unkillable_by_term_is_killed_after_grace():
    stubborn = [
        PY,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('x', flush=True); time.sleep(60)",
    ]

    async def go():
        o = make(engine=stubborn)
        o.stop_timeout_s = 0.5
        await start(o)
        await asyncio.sleep(0.3)  # let the child install its handler
        await o.stop_all()
        assert o.engine.state == "stopped" and o.engine.exit_code == -9

    asyncio.run(go())


def test_engine_crash_is_reported_not_restarted():
    codes: list[int | None] = []

    async def go():
        o = make(engine=[PY, "-c", "import sys; sys.exit(7)"], on_engine_exit=codes.append)
        await start(o)
        assert await until(lambda: o.engine.proc is not None and o.engine.proc.returncode is not None)
        await o.tick()
        assert codes == [7] and o.engine.state == "exited" and o.engine.restarts == 0
        await o.tick()
        assert codes == [7]  # reported once

    asyncio.run(go())


def test_spawn_failure_raises_oserror():
    async def go():
        o = make(engine=["/nonexistent/streamsarthi-engine"])
        with pytest.raises(OSError):
            await start(o)
        assert o.engine.state == "failed" and o.engine.reason == "spawn_failed"

    asyncio.run(go())


def test_disabled_orchestrator_spawns_nothing_and_attach_mode_skips_engine():
    async def go():
        off = Orchestrator(enabled=False, engine_cmd=SLEEP, chat_cmd=SLEEP)
        await start(off, chat_enabled=True)
        assert off.engine is None and off.chat is None
        o = make()
        await start(o, attach_to_running_engine=True)
        assert o.attached and o.engine is None
        await o.stop_all()

    asyncio.run(go())


def test_chat_codes_are_picked_out_and_nothing_else_is_kept():
    out = (
        "import sys,time\n"
        "print('secret message text 9876543210 should never be kept', flush=True)\n"
        "print('status groq_key_missing', flush=True)\n"
        "print('twitch_no_channel yt_login_required', flush=True)\n"
        "time.sleep(60)\n"
    )

    async def go():
        o = make(chat=[PY, "-c", out])
        await start(o, chat_enabled=True)
        assert await until(lambda: len(o.chat.codes) == 3)
        snap = o.snapshot()["chat"]
        assert snap["codes"] == ["groq_key_missing", "twitch_no_channel", "yt_login_required"]
        assert set(snap["codes"]) <= set(CHAT_STATUS_CODES)
        assert "9876543210" not in repr(snap)
        await o.stop_all()

    asyncio.run(go())


def test_chat_exit_codes_map_to_reasons_and_restart_policy():
    async def run(code: int, restarts_allowed: int = 2):
        o = make(chat=[PY, "-c", f"import sys; sys.exit({code})"], max_chat_restarts=restarts_allowed)
        await start(o, chat_enabled=True)
        end = time.monotonic() + 3
        while time.monotonic() < end:
            await o.tick()
            if o.chat.state == "failed" or (code == 0 and o.chat.state == "exited"):
                break
            await asyncio.sleep(0.05)
        snap = o.snapshot()["chat"]
        await o.stop_all()
        return snap

    async def go():
        ok = await run(0)
        assert ok["state"] == "exited" and ok["reason"] == "exited_ok" and ok["restarts"] == 0
        cfg = await run(1)
        assert cfg["state"] == "failed" and cfg["reason"] == "config_error" and cfg["restarts"] == 0
        bus = await run(3)  # restartable: retried, then given up
        assert bus["state"] == "failed" and bus["reason"] == "bus_error" and bus["restarts"] == 2
        assert (await run(4))["reason"] == "connectors_failed"
        assert (await run(2))["reason"] == "no_connector"

    asyncio.run(go())


def test_chat_failure_never_touches_the_engine():
    async def go():
        o = make(chat=[PY, "-c", "import sys; sys.exit(4)"])
        await start(o, chat_enabled=True)
        await asyncio.sleep(0.3)
        await o.tick()
        assert o.chat.state == "failed" and o.engine_running()
        await o.stop_all()

    asyncio.run(go())


def test_secrets_travel_in_env_only_and_never_in_snapshot_or_argv():
    seen: dict[str, object] = {}

    class FakeProc:
        returncode = None
        pid = None

        async def wait(self):
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    async def spawn(argv, env, *, capture=False):
        seen.setdefault("calls", []).append((list(argv), dict(env), capture))
        return FakeProc()

    async def go():
        o = Orchestrator(
            enabled=True,
            engine_cmd=["engine", "--profile", "{profile}"],
            chat_cmd=["chat", "--profile", "{profile}"],
            spawn=spawn,
            base_env={},
            chat_env={"BUS_SUB_ENDPOINT": "ipc:///x", "BUS_PUB_ENDPOINT": "ipc:///y"},
        )
        await start(
            o,
            profile="demo",
            chat_enabled=True,
            secret_env={"YOUTUBE_STREAM_KEY": SECRET_VALUE},
            ingest_env={"INGEST_PUBLISH_USER": "ssabc", "INGEST_PUBLISH_PASSWORD": "pw0123"},
        )
        (e_argv, e_env, e_cap), (c_argv, c_env, c_cap) = seen["calls"]
        assert e_argv == ["engine", "--profile", "demo"] and c_argv == ["chat", "--profile", "demo"]
        assert e_env["YOUTUBE_STREAM_KEY"] == SECRET_VALUE and e_env["INGEST_PUBLISH_USER"] == "ssabc"
        assert e_env["STREAMSARTHI_SESSION_ID"] == "sess-abc123" and e_cap is False and c_cap is True
        assert "YOUTUBE_STREAM_KEY" not in c_env and "INGEST_PUBLISH_PASSWORD" not in c_env
        assert c_env["BUS_SUB_ENDPOINT"] == "ipc:///x" and c_env["BUS_PUB_ENDPOINT"] == "ipc:///y"
        blob = repr(o.snapshot()) + repr(o.engine) + repr(o.chat) + repr(e_argv) + repr(c_argv)
        assert SECRET_VALUE not in blob and "pw0123" not in blob

    asyncio.run(go())


def test_ingest_login_is_random_and_url_shape_matches_the_relay():
    (u1, p1), (u2, p2) = new_ingest_login(), new_ingest_login()
    assert (u1, p1) != (u2, p2) and len(p1) >= 24
    urls = ingest_urls("127.0.0.1", u1, p1)
    assert urls["rtmp_url"] == f"rtmp://127.0.0.1:1935/camera?user={u1}&pass={p1}"
    assert urls["srt_url"].startswith(f"srt://127.0.0.1:8890?streamid=publish:camera:{u1}:{p1}")
