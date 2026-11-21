"""Token bucket, backoff, Secret, log helpers, fixed-origin HTTP, connector supervision, publisher."""

from __future__ import annotations

import asyncio
import logging
import pickle
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web

from chat.common import (
    Backoff,
    ChatError,
    ChatPublisher,
    Connector,
    FatalConnectorError,
    FinishedError,
    FixedOriginClient,
    ManualClock,
    RetryableError,
    Secret,
    SecretError,
    TokenBucket,
    hash_id,
    log_event,
)
from chat.common.http import validate_loopback_origin
from contracts import Category, ChatAction, ChatMessage, ChatVerdict, Modality, Topic
from contracts.fakes import InProcessBus


# ---- token bucket ----------------------------------------------------------------------------
def test_bucket_sync_refill_and_capacity(clock: ManualClock) -> None:
    bucket = TokenBucket(rate=0.5, capacity=30, clock=clock.monotonic, sleep=clock.sleep)
    assert all(bucket.try_acquire() for _ in range(30))  # starts full: a burst of 30
    assert not bucket.try_acquire()
    assert bucket.wait_time() == pytest.approx(2.0)
    clock.advance(2.0)
    assert bucket.try_acquire()
    clock.advance(10_000)
    assert bucket.tokens == 30  # never above capacity
    with pytest.raises(ValueError):
        bucket.try_acquire(31)


async def test_bucket_async_31st_request_waits(clock: ManualClock) -> None:
    bucket = TokenBucket(rate=0.5, capacity=30, clock=clock.monotonic, sleep=clock.sleep)
    for _ in range(30):
        await bucket.acquire()
    assert clock.sleeps == []
    await bucket.acquire()  # the 31st must wait for a refill
    assert clock.sleeps and clock.sleeps[0] == pytest.approx(2.0)


# ---- backoff ---------------------------------------------------------------------------------
def test_backoff_grows_is_capped_and_resets() -> None:
    backoff = Backoff(base=1, cap=10, factor=2, jitter=0.0)
    assert [backoff.next() for _ in range(6)] == [1, 2, 4, 8, 10, 10]
    backoff.reset()
    assert backoff.next() == 1


def test_backoff_jitter_stays_within_bounds() -> None:
    values = iter([0.0, 1.0, 0.5])
    backoff = Backoff(base=10, cap=100, jitter=0.2, rng=lambda: next(values))
    assert backoff.next() == pytest.approx(8.0)
    assert backoff.next() == pytest.approx(24.0)  # 20 * 1.2


# ---- secret ----------------------------------------------------------------------------------
def test_secret_never_prints_or_pickles() -> None:
    secret = Secret("tok-abc123")
    assert "abc123" not in repr(secret) and "abc123" not in str(secret) and "abc123" not in f"{secret!r:>20}"
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    for bad in ("", "has space", "ctl\x01", None):
        with pytest.raises(SecretError):
            Secret(bad)  # type: ignore[arg-type]


# ---- log helpers -----------------------------------------------------------------------------
def test_log_event_redacts_free_text(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("chat.test.logsafe")
    with caplog.at_level(logging.DEBUG, logger="chat.test.logsafe"):
        log_event(logger, "something_happened", mid=hash_id("m-1"), n=3, text="call me on 98765 43210")
        log_event(logger, "bad event with spaces")
    lines = [r.getMessage() for r in caplog.records]
    assert "98765" not in " ".join(lines)
    assert "text=<redacted>" in lines[0] and "n=3" in lines[0]
    assert lines[1] == "bad_event_code"


# ---- fixed-origin HTTP -----------------------------------------------------------------------
@pytest.fixture
async def server() -> AsyncIterator[str]:
    async def redirect(_: web.Request) -> web.Response:
        return web.Response(status=302, headers={"Location": "http://example.invalid/"})

    async def big(_: web.Request) -> web.Response:
        return web.Response(body=b'{"x":"' + b"a" * 5000 + b'"}', content_type="application/json")

    async def notjson(_: web.Request) -> web.Response:
        return web.Response(status=500, text="<html>oops</html>")

    async def echo(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/redirect", redirect)
    app.router.add_get("/big", big)
    app.router.add_get("/notjson", notjson)
    app.router.add_get("/echo", echo)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


def _client(aio: aiohttp.ClientSession, origin: str, **kwargs: object) -> FixedOriginClient:
    routes = {("GET", p): "o" for p in ("/redirect", "/big", "/notjson", "/echo")}
    return FixedOriginClient(
        aio, origins={"o": "https://real.example"}, routes=routes, loopback_origin=origin, **kwargs
    )  # type: ignore[arg-type]


async def test_http_allowlist_redirect_size_and_errors(aio: aiohttp.ClientSession, server: str) -> None:
    client = _client(aio, server, max_bytes=1000)
    assert (await client.request("GET", "/echo")).body == {"ok": True}
    with pytest.raises(ChatError) as unapproved:
        await client.request("GET", "/admin")
    assert unapproved.value.code == "http_unapproved"
    with pytest.raises(ChatError) as unapproved_method:
        await client.request("POST", "/echo")
    assert unapproved_method.value.code == "http_unapproved"
    with pytest.raises(ChatError) as redirected:
        await client.request("GET", "/redirect")
    assert redirected.value.code == "http_redirect"
    with pytest.raises(ChatError) as oversized:
        await client.request("GET", "/big")
    assert oversized.value.code == "http_oversized"
    # an error status with a non-JSON body is still a definitive status, not a transport failure
    assert (await client.request("GET", "/notjson")).status == 500


async def test_http_connection_failure_has_fixed_code(aio: aiohttp.ClientSession) -> None:
    client = _client(aio, "http://127.0.0.1:1")
    with pytest.raises(ChatError) as err:
        await client.request("GET", "/echo")
    assert err.value.code == "http_failed" and "127.0.0.1" not in str(err.value)


def test_loopback_origin_validation() -> None:
    assert validate_loopback_origin("http://127.0.0.1:8080")
    for bad in (
        "https://127.0.0.1:1",
        "http://localhost:1",
        "http://127.0.0.1",
        "http://127.0.0.1:1/x",
        "http://a@127.0.0.1:1",
    ):
        with pytest.raises(ValueError):
            validate_loopback_origin(bad)


# ---- connector supervision ---------------------------------------------------------------------
class _Scripted(Connector):
    def __init__(self, script: list[BaseException | None], **kwargs: object) -> None:
        super().__init__("scripted", **kwargs)  # type: ignore[arg-type]
        self.script = script

    async def _run_session(self) -> None:
        step = self.script.pop(0)
        if step is None:
            self.mark_healthy()
            await asyncio.sleep(3600)
        raise step


async def test_connector_retries_with_backoff_then_finishes(clock: ManualClock) -> None:
    statuses: list[str] = []
    conn = _Scripted(
        [
            RetryableError("e1"),
            RetryableError("e2"),
            RuntimeError("secret text 98765"),
            FinishedError("done"),
        ],
        clock=clock,
        backoff=Backoff(base=1, cap=60, jitter=0.0),
        on_status=statuses.append,
    )
    await conn.run()
    assert clock.sleeps == [1, 2, 4]  # exponential
    assert statuses == ["done"] and conn.state == "finished"
    assert conn.sessions_started == 4
    assert conn.last_error_code == "done"


async def test_connector_unexpected_error_code_is_class_name_only(clock: ManualClock) -> None:
    conn = _Scripted(
        [ValueError("viewer said something private"), FatalConnectorError("fatal_x")], clock=clock
    )
    await conn.run()
    assert conn.state == "failed"
    assert conn.last_error_code == "fatal_x"


async def test_connector_resets_backoff_after_healthy_session(clock: ManualClock) -> None:
    class Flaky(_Scripted):
        async def _run_session(self) -> None:
            step = self.script.pop(0)
            if isinstance(step, RetryableError) and step.code == "after_healthy":
                self.mark_healthy()
            raise step

    conn = Flaky(
        [
            RetryableError("a"),
            RetryableError("b"),
            RetryableError("after_healthy"),
            RetryableError("c"),
            FinishedError("end"),
        ],
        clock=clock,
        backoff=Backoff(base=1, cap=60, jitter=0.0),
    )
    await conn.run()
    assert clock.sleeps == [1, 2, 1, 2]  # healthy session reset the ladder


async def test_connector_stop_cancels_session_and_backoff(clock: ManualClock) -> None:
    conn = _Scripted([None], clock=clock)
    task = asyncio.ensure_future(conn.run())
    await asyncio.sleep(0.01)
    assert conn.state == "running"
    await conn.stop()
    await asyncio.wait_for(task, 1)
    assert conn.state == "stopped"


# ---- publisher ---------------------------------------------------------------------------------
async def test_publisher_topics_and_safety_events(clock: ManualClock) -> None:
    bus = InProcessBus()
    pub = ChatPublisher(bus, session_id="sess1", clock=clock)
    message = ChatMessage("twitch", "m1", "c1", "a1", "Name", "hello", 1.0)
    verdict = ChatVerdict("twitch", "m1", ChatAction.ALLOW, (), 0.1, "clean", "rules", 1.2)
    pub.allowed(message)
    pub.verdict(verdict)
    sink = pub.status_sink("twitch")
    sink("twitch_disconnected")
    sink("some_unreported_code")
    topics = [t for t, _ in bus.history]
    assert topics == [Topic.CHAT_ALLOWED, Topic.CHAT_VERDICT, Topic.SAFETY_EVENT]
    event = bus.history[2][1]
    assert event.detail_code == "twitch_disconnected" and event.modality is Modality.CHAT
    assert event.category is Category.SYSTEM_STATUS and event.source == "chat.twitch"


def test_session_id_comes_from_the_environment_when_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    from chat.common.bus import session_id_from_env

    monkeypatch.delenv("STREAMSARTHI_SESSION_ID", raising=False)
    assert session_id_from_env() == "chat"
    monkeypatch.setenv("STREAMSARTHI_SESSION_ID", "sess-2026_11-21")
    assert session_id_from_env() == "sess-2026_11-21"
    for bad in ("a" * 65, "has space", "semi;colon", "../x", ""):
        monkeypatch.setenv("STREAMSARTHI_SESSION_ID", bad)
        assert session_id_from_env() == "chat", bad
    monkeypatch.setenv("STREAMSARTHI_SESSION_ID", "s123")
    bus = InProcessBus()
    ChatPublisher(bus, session_id=session_id_from_env()).safety("twitch_disconnected")
    assert bus.history[0][1].session_id == "s123"
