"""Anonymous read-only Twitch IRC reader against the loopback fake."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import aiohttp
import pytest

from chat.common import Backoff, ManualClock
from chat.testing import until
from chat.testing.fake_irc import FakeIrc, privmsg
from chat.twitch.irc import AnonymousIrcReader, irc_url, parse_line
from contracts import ChatMessage


@pytest.fixture
async def irc() -> AsyncIterator[FakeIrc]:
    fake = FakeIrc()
    await fake.start()
    yield fake
    await fake.stop()


def reader(fake: FakeIrc, aio: aiohttp.ClientSession, out: list[ChatMessage], statuses: list[str]):  # type: ignore[no-untyped-def]
    return AnonymousIrcReader(
        "SpecimenChan",
        out.append,
        aio,
        url=fake.url,
        mock=True,
        clock=ManualClock(),
        backoff=Backoff(base=0.01, cap=0.02, jitter=0.0),
        on_status=statuses.append,
    )


def test_parse_line_tags_command_and_trailing() -> None:
    tags, cmd, params = parse_line(
        r"@badges=moderator/1;display-name=A\sB;id=x :a!a@a.tmi.twitch.tv PRIVMSG #c :hi there : ok"
    )
    assert cmd == "PRIVMSG" and params == ["#c", "hi there : ok"]
    assert tags["display-name"] == "A B" and tags["badges"] == "moderator/1"
    assert parse_line("PING :tmi.twitch.tv")[1:] == ("PING", ["tmi.twitch.tv"])


def test_only_the_documented_endpoints_are_accepted() -> None:
    assert irc_url("wss://irc-ws.chat.twitch.tv:443")
    for bad in ("wss://evil.example/", "ws://irc-ws.chat.twitch.tv", "wss://u:p@irc-ws.chat.twitch.tv"):
        with pytest.raises(ValueError):
            irc_url(bad)
    with pytest.raises(ValueError):
        irc_url("wss://irc-ws.chat.twitch.tv", mock=True)


def test_invalid_channel_login_is_refused(aio) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        AnonymousIrcReader("bad name;", lambda m: None, aio, url="ws://127.0.0.1:1", mock=True)


async def test_reads_messages_with_roles_from_badges_and_dedupes(irc: FakeIrc, aio) -> None:  # type: ignore[no-untyped-def]
    irc.script = [
        [
            privmsg("m1", "7", "namaste"),
            privmsg("m2", "8", "!hold", badges="moderator/1,subscriber/6"),
            privmsg("m2", "8", "!hold", badges="moderator/1"),  # redelivery
            privmsg("m3", "1001", "hi", badges="broadcaster/1"),
            "@id=x PING :tmi.twitch.tv",
            privmsg("m4", "9", "\x01ACTION waves\x01"),
        ]
    ]
    out: list[ChatMessage] = []
    r = reader(irc, aio, out, [])
    import asyncio

    task = asyncio.create_task(r.run())
    await until(lambda: len(out) == 4)
    await r.stop()
    await task
    assert [m.message_id for m in out] == ["m1", "m2", "m3", "m4"]
    assert (out[0].is_moderator, out[0].is_broadcaster) == (False, False)
    assert out[1].is_moderator and not out[1].is_broadcaster
    assert out[2].is_broadcaster
    assert out[3].text == "waves" and out[0].platform == "twitch" and out[0].channel_id == "1001"
    sent = irc.received
    assert "PASS SCHMOOPIIE" in sent and "JOIN #specimenchan" in sent
    assert any(s.startswith("NICK justinfan") for s in sent)  # anonymous, no token anywhere


async def test_pong_is_sent_and_reconnect_reports_status_once(irc: FakeIrc, aio) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    irc.script = [[privmsg("a1", "7", "one"), "<close>"], [privmsg("a2", "7", "two")]]
    out: list[ChatMessage] = []
    statuses: list[str] = []
    r = reader(irc, aio, out, statuses)
    task = asyncio.create_task(r.run())
    await until(lambda: len(out) == 2)
    await irc.push("PING :keepalive")
    await until(lambda: "PONG :keepalive" in irc.received)
    await r.stop()
    await task
    assert irc.connections == 2 and [m.message_id for m in out] == ["a1", "a2"]
    assert statuses == ["twitch_disconnected", "twitch_reconnected"]  # never a hold


async def test_connect_failure_retries_without_raising(aio) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    statuses: list[str] = []
    r = AnonymousIrcReader(
        "chan",
        lambda m: None,
        aio,
        url="ws://127.0.0.1:1",
        mock=True,
        clock=ManualClock(),
        backoff=Backoff(base=0.01, cap=0.02, jitter=0.0),
        on_status=statuses.append,
    )
    task = asyncio.create_task(r.run())
    await until(lambda: r.sessions_started >= 3)
    await r.stop()
    await task
    assert statuses == ["twitch_disconnected"] and r.last_error_code == "twitch_irc_connect_failed"


async def test_message_text_is_never_logged(irc: FakeIrc, aio, caplog) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    irc.script = [[privmsg("z1", "7", "SECRET-TEXT-123 my number 9876543210", name="SecretName")]]
    out: list[ChatMessage] = []

    def boom(m: ChatMessage) -> None:
        out.append(m)
        raise RuntimeError(m.text)  # a faulty consumer must not leak text through the connector

    r = AnonymousIrcReader("chan", boom, aio, url=irc.url, mock=True, clock=ManualClock())
    with caplog.at_level(logging.DEBUG):
        task = asyncio.create_task(r.run())
        await until(lambda: len(out) == 1)
        await r.stop()
        await task
    assert (
        "SECRET-TEXT" not in caplog.text
        and "9876543210" not in caplog.text
        and "SecretName" not in caplog.text
    )
