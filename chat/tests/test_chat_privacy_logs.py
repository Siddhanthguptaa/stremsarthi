"""Privacy rule (docs/specs/chat.md 5.6): no message text, author name, token or secret in any log line.

Runs both connectors through success and failure paths (including a consumer that raises with the
message text in its exception) with DEBUG capture on the root logger, then scans every record.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from chat.common import Backoff, ChatPublisher
from chat.testing.waiting import until
from chat.twitch import ChatEvents, EventSubTransport, TwitchActions
from chat.youtube import YouTubeActions, YouTubePoller
from contracts import Category, ChatAction, ChatMessage, ChatVerdict
from contracts.fakes import InProcessBus

PHONE = "9876543210"
TEXT = f"PRIVMARK call me {PHONE} or pay upi://pay?pa=scammer@ybl&am=500"
AUTHOR = "AuthorNameMarkerXyz"
STREAMER_TERM = "StreamerRealCityMarker"


def assert_clean(caplog: pytest.LogCaptureFixture, secrets: list[str]) -> None:
    assert caplog.records, "nothing was logged: the test would prove nothing"
    blobs = [caplog.text]
    for record in caplog.records:
        blobs += [record.getMessage(), str(record.args), record.exc_text or "", str(record.exc_info or "")]
    haystack = "\n".join(blobs)
    for needle in ["PRIVMARK", PHONE, "scammer@ybl", "upi://", AUTHOR, STREAMER_TERM, *secrets]:
        assert needle not in haystack, f"{needle!r} leaked into a log record"


async def test_no_flagged_text_or_secret_reaches_the_logs(
    caplog: pytest.LogCaptureFixture, yt: SimpleNamespace, tw: SimpleNamespace
) -> None:
    caplog.set_level(logging.DEBUG)
    secrets = [
        yt.tokens.access.reveal(),
        yt.tokens.refresh.reveal(),
        tw.tokens.access.reveal(),
        tw.tokens.refresh.reveal(),
        "specimen-secret",
    ]
    bus = InProcessBus()
    publisher = ChatPublisher(bus, clock=yt.clock)

    # ---- YouTube: poll, a consumer that raises with the text, a 503, delete, quota-low ----------
    for i in range(3):
        yt.fake.add_text(TEXT, display=AUTHOR, published_at="2023-11-14T22:13:00Z", message_id=f"yt{i}")
    seen: list[ChatMessage] = []

    def consumer(message: ChatMessage) -> None:
        seen.append(message)
        raise RuntimeError(f"consumer failed on {message.text} by {message.author_display}")

    poller = YouTubePoller(
        yt.client,
        consumer,
        live_chat_id=yt.fake.live_chat_id,
        clock=yt.clock,
        backoff=Backoff(jitter=0.0),
        on_status=publisher.status_sink("youtube"),
    )
    yt.fake.inject(503, reason="backendError", path="/youtube/v3/liveChat/messages")
    ptask = asyncio.ensure_future(poller.run())
    await until(lambda: len(seen) == 3)
    await poller.stop()
    await asyncio.wait_for(ptask, 2)
    yt_actions = YouTubeActions(yt.client, get_live_chat_id=lambda: yt.fake.live_chat_id, clock=yt.clock)
    results = [await yt_actions.delete(m) for m in seen]
    assert all(r.status == "done" for r in results)
    yt.fake.inject(403, reason="insufficientPermissions", path="/youtube/v3/liveChat/messages")
    assert (await yt_actions.delete(seen[0])).status == "failed"
    verdict = ChatVerdict(
        "youtube",
        seen[0].message_id,
        ChatAction.DELETE,
        (Category.CHAT_FAKE_UPI,),
        0.98,
        "fake_upi_link",
        "rules",
        3.0,
    )
    publisher.verdict(verdict)

    # ---- Twitch: events, a raising consumer, transport, actions failing ---------------------------
    tw_seen: list[ChatMessage] = []

    def tw_consumer(message: ChatMessage) -> None:
        tw_seen.append(message)
        raise RuntimeError(f"twitch consumer failed on {message.text} by {message.author_display}")

    events = ChatEvents(tw.fake.user_id, tw_consumer, publisher.status_sink("twitch"), mock=True)
    transport = EventSubTransport(
        events,
        tw.session,
        tw.http,
        tw.aio,
        broadcaster_id=tw.fake.user_id,
        url=tw.fake.ws_url,
        mock=True,
        keepalive_grace_s=0.3,
        clock=tw.clock,
        backoff=Backoff(jitter=0.0),
        on_status=publisher.status_sink("twitch"),
    )
    ttask = asyncio.ensure_future(transport.run())
    await tw.fake.wait_subscribed()
    await tw.fake.push_chat(TEXT, chatter_name=AUTHOR, message_id="tw1")
    await tw.fake.push_chat(f"{STREAMER_TERM} {TEXT}", chatter_name=AUTHOR, message_id="tw2")
    await until(lambda: len(tw_seen) == 2)
    await tw.fake.drop()  # outage path
    await until(lambda: tw.fake.connections >= 2)
    await transport.stop()
    await asyncio.wait_for(ttask, 3)
    tw_actions = TwitchActions(tw.http, tw.session, tw.fake.user_id, clock=tw.clock)
    tw.fake.fail_next_helix(502)
    await tw_actions.delete(tw_seen[0])
    await tw_actions.timeout(tw_seen[1], 600)

    assert_clean(caplog, secrets)
    # the scan is meaningful: connector and action activity really was logged
    names = {r.name for r in caplog.records}
    assert {"chat.youtube", "chat.youtube.actions", "chat.twitch.events", "chat.twitch.actions"} <= names


def test_exceptions_from_chat_code_carry_codes_not_text() -> None:
    from chat.common import ChatError

    err = ChatError("http_failed")
    assert str(err) == "http_failed" and err.args == ("http_failed",)
