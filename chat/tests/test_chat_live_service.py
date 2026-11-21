"""``chat.live.ChatService`` and ``chat.main.amain`` on the fake platforms and the in-process bus: connectors per
config, missing credentials degrade safely, hold from chat reaches the bus, clean shutdown, exit codes.
Live checks (real keys) are skipped unless ``STREAMSARTHI_LIVE_CHAT`` is set."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from chat.live import ChatService, ExitCode, LiveOptions
from chat.main import amain
from chat.testing.waiting import until
from contracts import ChatAction, Config, HoldCommand, Topic
from contracts.config import ChatConfig, TwitchChatConfig
from contracts.fakes import InProcessBus


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def config(**chat: object) -> Config:
    chat.setdefault("enabled", True)
    return Config(
        chat=ChatConfig(**chat),  # type: ignore[arg-type]
        groq_api_key=None,
        youtube_oauth_client_id="specimen-client-id",
        twitch_client_id="specimen-twitch-client",
    )


@pytest.fixture(autouse=True)
def no_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


async def stop_and_wait(service: ChatService, task: asyncio.Future[int]) -> int:
    service.request_stop()
    return await asyncio.wait_for(task, 10)


async def test_disabled_chat_exits_ok_without_touching_anything() -> None:
    service = ChatService(config(enabled=False), InProcessBus())
    assert await asyncio.wait_for(service.run(), 5) == ExitCode.OK
    assert service.platforms == []


async def test_no_credentials_exits_with_a_clear_status_and_logs_codes_only(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    cfg = Config(
        chat=ChatConfig(enabled=True), groq_api_key=None, youtube_oauth_client_id=None, twitch_client_id=None
    )
    service = ChatService(cfg, InProcessBus(), LiveOptions(youtube_tokens=_none, twitch_tokens=_none))
    assert await asyncio.wait_for(service.run(), 5) == ExitCode.NO_CONNECTORS
    assert service.skipped == {"youtube": "yt_no_credentials", "twitch": "twitch_no_credentials"}
    text = caplog.text
    assert "groq_key_missing" in text and "chat_no_connectors" in text  # rules-only degrade is announced
    assert "yt_no_credentials" in text and "twitch_no_credentials" in text


async def _none(_auth: object) -> None:
    return None


async def test_no_stored_token_without_login_skips_that_platform(yt: SimpleNamespace) -> None:
    cfg = config(platforms=["youtube"])
    service = ChatService(
        cfg, InProcessBus(), LiveOptions(youtube_origin=yt.fake.origin, youtube_tokens=_none)
    )
    assert await asyncio.wait_for(service.run(), 5) == ExitCode.NO_CONNECTORS
    assert service.skipped == {"youtube": "yt_login_required"}


async def test_anonymous_irc_needs_a_channel() -> None:
    cfg = config(platforms=["twitch"], twitch=TwitchChatConfig(anonymous_read=True, channel_login=None))
    service = ChatService(cfg, InProcessBus())
    assert await asyncio.wait_for(service.run(), 5) == ExitCode.NO_CONNECTORS
    assert service.skipped == {"twitch": "twitch_no_channel"}


async def test_youtube_end_to_end_rules_only_and_clean_shutdown(yt: SimpleNamespace) -> None:
    yt.fake.add_text("bahut badhiya stream bhai", published_at=now_iso(), message_id="ok1")
    yt.fake.add_text("tu madarchod chutiya hai", published_at=now_iso(), message_id="bad1")
    bus = InProcessBus()
    sub = bus.subscribe([Topic.CHAT_ALLOWED, Topic.CHAT_VERDICT])

    async def tokens(_auth: object):  # type: ignore[no-untyped-def]
        return yt.tokens

    service = ChatService(
        config(platforms=["youtube"]),
        bus,
        LiveOptions(youtube_origin=yt.fake.origin, youtube_tokens=tokens),
    )
    task = asyncio.ensure_future(service.run())
    try:
        await service.wait_ready()
        await until(lambda: len(sub) >= 3, 8.0)  # two verdicts and one allowed message
    finally:
        code = await stop_and_wait(service, task)
    assert code == ExitCode.OK
    events = [sub.get_nowait() for _ in range(len(sub))]
    allowed = [e.message_id for t, e in events if t == Topic.CHAT_ALLOWED]
    verdicts = {e.message_id: e for t, e in events if t == Topic.CHAT_VERDICT}
    assert len(allowed) == 1 and verdicts.keys() >= {allowed[0]}
    flagged = [v for v in verdicts.values() if v.action != ChatAction.ALLOW]
    assert flagged and all(v.message_id not in allowed for v in flagged)
    assert service.pipeline is not None and service.pipeline.classifier.llm_stage is None  # rules only


async def test_twitch_hold_command_reaches_the_bus_and_flagged_text_stays_hidden(tw: SimpleNamespace) -> None:
    bus = InProcessBus()
    sub = bus.subscribe([Topic.HOLD, Topic.CHAT_ALLOWED])

    async def tokens(_auth: object):  # type: ignore[no-untyped-def]
        return tw.tokens

    service = ChatService(
        config(platforms=["twitch"], twitch=TwitchChatConfig()),
        bus,
        LiveOptions(
            twitch_origin=tw.fake.origin,
            twitch_ws_url=tw.fake.ws_url,
            twitch_mock_ws=True,
            twitch_tokens=tokens,
        ),
    )
    task = asyncio.ensure_future(service.run())
    try:
        await service.wait_ready()
        await tw.fake.wait_subscribed()
        await tw.fake.push_chat("!hold", chatter_id="3003", message_id="c1", badges=["moderator"])
        await tw.fake.push_chat("!hold", chatter_id="4004", message_id="c2")  # a viewer: ignored
        await tw.fake.push_chat("namaste sabko", chatter_id="4004", message_id="c3")
        await until(lambda: len(sub) >= 2, 8.0)
    finally:
        code = await stop_and_wait(service, task)
    assert code == ExitCode.OK
    events = [sub.get_nowait() for _ in range(len(sub))]
    holds = [e for t, e in events if t == Topic.HOLD]
    assert len(holds) == 1 and isinstance(holds[0], HoldCommand) and holds[0].active is True
    assert [e.message_id for t, e in events if t == Topic.CHAT_ALLOWED] == ["c3"]  # commands are silent


async def test_all_connectors_failing_for_good_exits_connector_failed(tw: SimpleNamespace) -> None:
    async def tokens(_auth: object):  # type: ignore[no-untyped-def]
        return tw.tokens

    service = ChatService(
        config(platforms=["twitch"]),
        InProcessBus(),
        LiveOptions(
            twitch_origin=tw.fake.origin,
            twitch_ws_url=tw.fake.ws_url,
            twitch_mock_ws=True,
            twitch_tokens=tokens,
        ),
    )
    task = asyncio.ensure_future(service.run())
    try:
        await service.wait_ready()
        await tw.fake.wait_subscribed()
        await tw.fake.send_revocation()
        assert await asyncio.wait_for(task, 10) == ExitCode.CONNECTOR_FAILED
    finally:
        service.request_stop()


async def test_amain_config_error_and_disabled(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert (
        await amain(["--config-dir", str(tmp_path / "missing")], install_signals=False)
        == ExitCode.CONFIG_ERROR
    )
    cfg = Config(chat=ChatConfig(enabled=False), groq_api_key=None)
    assert await amain([], bus=InProcessBus(), config=cfg, install_signals=False) == ExitCode.OK


async def test_secrets_never_appear_in_logs(yt: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    cfg = Config(
        chat=ChatConfig(enabled=True, platforms=["youtube"]),
        groq_api_key=None,
        youtube_oauth_client_id="specimen-client-id",
        youtube_oauth_client_secret="SPECIMEN-SECRET-VALUE",
    )
    service = ChatService(
        cfg, InProcessBus(), LiveOptions(youtube_origin=yt.fake.origin, youtube_tokens=_none)
    )
    await asyncio.wait_for(service.run(), 5)
    assert "SPECIMEN-SECRET-VALUE" not in caplog.text and "specimen-client-id" not in caplog.text


@pytest.mark.skipif(not os.environ.get("STREAMSARTHI_LIVE_CHAT"), reason="live chat check needs real keys")
async def test_live_connectors_start_with_real_credentials() -> None:  # pragma: no cover - needs keys
    from contracts import load_config

    cfg = load_config()
    service = ChatService(cfg, InProcessBus(), LiveOptions(login=False))
    task = asyncio.ensure_future(service.run())
    await asyncio.sleep(5)
    assert service.platforms or service.skipped  # connected, or skipped with a code
    service.request_stop()
    await asyncio.wait_for(task, 15)
