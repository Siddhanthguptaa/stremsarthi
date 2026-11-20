"""EventSub transport against the fake Twitch server: welcome, subscribe, keepalive, reconnect, handover."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from chat.common import Backoff, ChatPublisher
from chat.testing.waiting import until
from chat.twitch import EVENTSUB_URL, ChatEvents, EventSubTransport, TwitchActions
from contracts import ChatMessage, Topic
from contracts.fakes import InProcessBus


def build(tw: SimpleNamespace, **kwargs: object) -> SimpleNamespace:
    got: list[ChatMessage] = []
    bus = InProcessBus()
    publisher = ChatPublisher(bus, clock=tw.clock)
    sink = publisher.status_sink("twitch")
    statuses: list[str] = []

    def on_status(code: str) -> None:
        statuses.append(code)
        sink(code)

    events = ChatEvents(tw.fake.user_id, got.append, on_status, mock=True)
    params: dict[str, object] = {
        "broadcaster_id": tw.fake.user_id,
        "url": tw.fake.ws_url,
        "mock": True,
        "keepalive_grace_s": 0.3,
        "retire_grace_s": 0.2,
        "clock": tw.clock,
        "backoff": Backoff(base=1, cap=4, jitter=0.0),
        "on_status": on_status,
    }
    params.update(kwargs)
    transport = EventSubTransport(events, tw.session, tw.http, tw.aio, **params)  # type: ignore[arg-type]
    return SimpleNamespace(got=got, bus=bus, statuses=statuses, events=events, transport=transport)


async def start(env: SimpleNamespace) -> asyncio.Future[None]:
    return asyncio.ensure_future(env.transport.run())


async def finish(env: SimpleNamespace, task: asyncio.Future[None]) -> None:
    await env.transport.stop()
    await asyncio.wait_for(task, 3)


async def test_welcome_subscribe_then_messages(tw: SimpleNamespace) -> None:
    env = build(tw)
    task = await start(env)
    try:
        await tw.fake.wait_subscribed()
        await until(lambda: env.events.phase == "ready")
        await tw.fake.push_chat("namaste doston", message_id="m1", chatter_name="Asha")
        await until(lambda: len(env.got) == 1)
    finally:
        await finish(env, task)
    assert env.got[0].text == "namaste doston" and env.got[0].author_display == "Asha"
    (sub,) = tw.fake.subscriptions
    assert sub["type"] == "channel.chat.message" and sub["version"] == "1"
    assert sub["condition"] == {"broadcaster_user_id": tw.fake.user_id, "user_id": tw.fake.user_id}
    assert sub["transport"]["method"] == "websocket"
    headers = tw.fake.helix_headers[0]
    assert headers["Client-Id"] == tw.fake.client_id and headers["Authorization"].startswith("Bearer access-")


async def test_keepalive_timeout_reconnects_without_duplicate_delivery(tw: SimpleNamespace) -> None:
    env = build(tw)
    task = await start(env)
    try:
        await tw.fake.wait_subscribed()
        first = await tw.fake.push_chat("one", message_id="m1")
        await until(lambda: len(env.got) == 1)
        tw.fake.silence()  # no keepalives any more: the client must notice and reconnect
        await until(lambda: tw.fake.connections >= 2, timeout_s=10)
        await tw.fake.wait_subscribed()
        await until(lambda: env.events.phase == "ready")
        await tw.fake.push(first)  # the platform redelivers the same notification on the new session
        await tw.fake.push_chat("two", message_id="m2")
        await until(lambda: len(env.got) == 2)
        await asyncio.sleep(0.1)
    finally:
        await finish(env, task)
    assert [m.message_id for m in env.got] == ["m1", "m2"]  # m1 exactly once
    assert env.statuses[:2] == ["twitch_disconnected", "twitch_reconnected"]
    assert env.transport.last_error_code == "twitch_keepalive_timeout"
    assert len(tw.fake.subscriptions) == 2  # a fresh session is subscribed again


async def test_dropped_socket_never_asserts_a_hold(tw: SimpleNamespace) -> None:
    env = build(tw)
    task = await start(env)
    try:
        await tw.fake.wait_subscribed()
        await tw.fake.drop()
        await until(lambda: env.statuses[:1] == ["twitch_disconnected"])
        await until(lambda: tw.fake.connections >= 2)
        await tw.fake.wait_subscribed()
        await tw.fake.push_chat("after the drop", message_id="m9")
        await until(lambda: len(env.got) == 1)
    finally:
        await finish(env, task)
    topics = [t for t, _ in env.bus.history]
    assert Topic.HOLD not in topics  # IRL Shield asserted a hold here; chat must not
    events = [e for t, e in env.bus.history if t is Topic.SAFETY_EVENT]
    assert [e.detail_code for e in events] == ["twitch_disconnected", "twitch_reconnected"]
    assert all(e.source == "chat.twitch" for e in events)


async def test_session_reconnect_hands_over_without_loss_or_duplicates(tw: SimpleNamespace) -> None:
    env = build(tw)
    task = await start(env)
    try:
        await tw.fake.wait_subscribed()
        first_session = env.events.session_id
        await tw.fake.send_reconnect()
        await until(
            lambda: env.events.session_id not in (None, first_session) and env.events.phase == "ready"
        )
        await tw.fake.wait_subscribed(2)  # old socket is still open for a moment: both receive the push
        await tw.fake.push_chat("during handover", message_id="m1")
        await until(lambda: len(env.got) >= 1)
        await asyncio.sleep(0.4)  # old socket retires
        await tw.fake.push_chat("after handover", message_id="m2")
        await until(lambda: len(env.got) == 2)
    finally:
        await finish(env, task)
    assert [m.message_id for m in env.got] == ["m1", "m2"]
    assert len(tw.fake.subscriptions) == 1  # subscriptions carried over: no re-subscribe on handover
    assert env.statuses == []  # a planned handover is not an outage
    assert env.transport.sessions_started == 1


async def test_revocation_stops_the_connector_for_good(tw: SimpleNamespace) -> None:
    env = build(tw)
    task = await start(env)
    await tw.fake.wait_subscribed()
    await tw.fake.send_revocation()
    await asyncio.wait_for(task, 5)
    assert env.transport.state == "failed" and "twitch_subscription_revoked" in env.statuses
    assert env.events.phase == "revoked" and tw.fake.connections == 1


async def test_missing_scope_on_subscribe_is_fatal_not_a_retry_storm(tw: SimpleNamespace) -> None:
    tw.fake.subscribe_status = 403
    env = build(tw)
    await asyncio.wait_for(env.transport.run(), 5)
    assert env.transport.state == "failed" and env.transport.last_error_code == "twitch_forbidden"
    assert tw.fake.connections == 1


async def test_revoked_token_is_fatal(tw: SimpleNamespace) -> None:
    tw.clock.advance(3601)  # force a re-validation
    tw.fake.valid_tokens.clear()
    env = build(tw)
    await asyncio.wait_for(env.transport.run(), 5)
    assert env.transport.state == "failed" and env.transport.last_error_code == "twitch_auth_failed"
    assert "twitch_disconnected" in env.statuses


async def test_welcome_timeout_retries_with_backoff(tw: SimpleNamespace) -> None:
    tw.fake.welcome_delay_s = 1.0
    env = build(tw, welcome_timeout_s=0.2)
    task = await start(env)
    try:
        await until(lambda: env.transport.sessions_started >= 2)
    finally:
        await finish(env, task)
    assert env.transport.last_error_code == "twitch_welcome_timeout" and tw.clock.sleeps[:1] == [1.0]
    assert env.got == []


async def test_stop_closes_sockets_and_leaves_no_tasks(tw: SimpleNamespace) -> None:
    env = build(tw)
    task = await start(env)
    await tw.fake.wait_subscribed()
    before = {t for t in asyncio.all_tasks() if not t.done()}
    await finish(env, task)
    await until(lambda: not tw.fake.live_sockets())
    leftovers = {t for t in asyncio.all_tasks() if not t.done()} - before
    assert env.transport.state == "stopped"
    assert all("_pump" not in repr(t) and "_retire" not in repr(t) for t in leftovers)


def test_real_endpoint_is_validated_and_mock_endpoint_needs_the_flag(tw: SimpleNamespace) -> None:
    events = ChatEvents("1", lambda m: None)
    assert EventSubTransport(events, tw.session, tw.http, tw.aio, broadcaster_id="1").url == EVENTSUB_URL
    with pytest.raises(ValueError):
        EventSubTransport(events, tw.session, tw.http, tw.aio, broadcaster_id="1", url="ws://127.0.0.1:1/ws")
    with pytest.raises(ValueError):
        EventSubTransport(
            events, tw.session, tw.http, tw.aio, broadcaster_id="1", url="wss://evil.example/ws"
        )


async def test_actions_share_the_same_session_and_user(tw: SimpleNamespace) -> None:
    actions = TwitchActions(tw.http, tw.session, tw.fake.user_id, clock=tw.clock)
    assert actions.blocked_reason is None
