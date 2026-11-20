"""EventSub policy: validation, de-duplication, generation fencing, phases, status codes, mapping."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from chat.testing import FakeTwitch
from chat.twitch import ChatEvents
from chat.twitch.events import websocket_url
from contracts import ChatMessage

Factory = Callable[..., tuple[ChatEvents, list[ChatMessage], list[str]]]
B = "1001"  # broadcaster id used by FakeTwitch


def ready(events: ChatEvents) -> int:
    gen = events.connection_started()
    assert events.welcome("s1", generation=gen)
    assert events.ready(generation=gen)
    return gen


def note(text: str = "hello", **kwargs: Any) -> dict[str, Any]:
    return FakeTwitch().notification(text, **kwargs)


def test_maps_a_notification_to_chat_message(twitch_events_factory: Factory) -> None:
    events, got, _ = twitch_events_factory(B, clock=lambda: 123.0)
    gen = ready(events)
    env = note("namaste", chatter_id="2002", chatter_name="Asha", message_id="msg-1", badges=["moderator"])
    assert events.accept(env, generation=gen) == "delivered"
    (message,) = got
    assert (message.platform, message.message_id, message.channel_id) == ("twitch", "msg-1", B)
    assert (message.author_id, message.author_display, message.text) == ("2002", "Asha", "namaste")
    assert message.is_moderator and not message.is_broadcaster and message.received_at == 123.0
    owner = note("hi", chatter_id=B, message_id="msg-2")
    events.accept(owner, generation=gen)
    assert got[1].is_broadcaster


def test_not_accepted_until_welcomed(twitch_events_factory: Factory) -> None:
    events, got, _ = twitch_events_factory(B)
    assert events.accept(note(), generation=events.generation) == "inactive"
    gen = events.connection_started()
    assert events.accept(note(), generation=gen) == "inactive"  # still connecting
    events.welcome("s1", generation=gen)
    assert events.accept(note(message_id="a"), generation=gen) == "delivered"  # subscribing already accepts
    assert len(got) == 1


def test_duplicate_notification_and_duplicate_chat_message(twitch_events_factory: Factory) -> None:
    events, got, _ = twitch_events_factory(B)
    gen = ready(events)
    env = note("once", message_id="m1", notification_id="n1")
    assert events.accept(env, generation=gen) == "delivered"
    assert events.accept(copy.deepcopy(env), generation=gen) == "duplicate"  # same notification id
    again = note(
        "once", message_id="m1", notification_id="n2"
    )  # re-subscription redelivery, new notification id
    assert events.accept(again, generation=gen) == "duplicate"
    assert events.accept(note("other", message_id="m2", notification_id="n1"), generation=gen) == "duplicate"
    assert [m.message_id for m in got] == ["m1"]


def test_replay_cache_is_bounded_by_age_and_capacity(twitch_events_factory: Factory) -> None:
    now = [1000.0]
    events, got, _ = twitch_events_factory(B, clock=lambda: now[0], capacity=4, max_age=60)
    gen = ready(events)
    for i in range(6):
        events.accept(note(message_id=f"m{i}", notification_id=f"n{i}"), generation=gen)
    assert events.remembered <= 4  # capacity: oldest keys evicted
    now[0] += 61
    assert (
        events.accept(note(message_id="m5", notification_id="n5"), generation=gen) == "delivered"
    )  # aged out
    assert len(got) == 7


def test_generation_fencing_drops_superseded_connections(twitch_events_factory: Factory) -> None:
    events, got, _ = twitch_events_factory(B)
    old = ready(events)
    new = events.connection_started()
    events.welcome("s2", generation=new)
    assert events.accept(note(message_id="late"), generation=old) == "stale"
    assert events.welcome("s1", generation=old) is False and events.ready(generation=old) is False
    assert events.accept(note(message_id="ok"), generation=new) == "delivered"
    assert [m.message_id for m in got] == ["ok"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e["metadata"].update(message_type="session_keepalive"),
        lambda e: e["metadata"].update(subscription_type="channel.follow"),
        lambda e: e["metadata"].update(subscription_version="2"),
        lambda e: e["payload"]["subscription"].update(status="revoked"),
        lambda e: e["payload"]["subscription"]["condition"].update(broadcaster_user_id="777"),
        lambda e: e["payload"]["event"].update(broadcaster_user_id="777"),
        lambda e: e["metadata"].update(message_id=""),
        lambda e: e["metadata"].update(message_id="x" * 300),
        lambda e: e["payload"]["event"].pop("message"),
        lambda e: e["payload"]["event"].update(message_id="has spaces in it"),
        lambda e: e["payload"].pop("event"),
        lambda e: e.pop("metadata"),
    ],
)
def test_invalid_envelopes_are_refused(
    twitch_events_factory: Factory, mutate: Callable[[dict[str, Any]], object]
) -> None:
    events, got, _ = twitch_events_factory(B)
    gen = ready(events)
    env = note("hello")
    mutate(env)
    assert events.accept(env, generation=gen) == "invalid"
    assert got == []


def test_shared_chat_from_another_channel_is_ignored(twitch_events_factory: Factory) -> None:
    events, got, _ = twitch_events_factory(B)
    gen = ready(events)
    assert events.accept(note(source_broadcaster_id="555"), generation=gen) == "ignored"
    assert events.accept(note(source_broadcaster_id=B, message_id="own"), generation=gen) == "delivered"
    assert len(got) == 1


def test_disconnect_reports_status_only_and_never_a_hold(twitch_events_factory: Factory) -> None:
    events, _, statuses = twitch_events_factory(B)
    assert not hasattr(events, "hold")
    gen = ready(events)
    events.fault("socket-closed")
    events.fault("connect-failed")  # repeated failures during one outage are not re-reported
    assert statuses == ["twitch_disconnected"]
    assert events.phase == "disconnected" and events.accept(note(), generation=gen) == "stale"
    gen2 = events.connection_started()
    events.welcome("s2", generation=gen2)
    events.ready(generation=gen2)
    assert statuses == ["twitch_disconnected", "twitch_reconnected"]
    events.fault("again")
    assert statuses[-1] == "twitch_disconnected"  # a new outage is reported again


def test_revocation_is_terminal(twitch_events_factory: Factory) -> None:
    events, _, statuses = twitch_events_factory(B)
    ready(events)
    events.fault("revoked", revoked=True)
    assert "twitch_subscription_revoked" in statuses and events.phase == "revoked"
    new = events.connection_started()
    assert events.welcome("s9", generation=new) is False  # a revoked session never comes back


def test_reconnect_handover_phases(twitch_events_factory: Factory) -> None:
    events, got, _ = twitch_events_factory(B, mock=True)
    gen = ready(events)
    assert events.reconnect("ws://127.0.0.1:9/ws?x=1", generation=gen)
    assert events.phase == "handover"
    assert (
        events.accept(note(message_id="in-flight"), generation=gen) == "delivered"
    )  # old socket still delivers
    assert events.handed_over("s2", generation=gen) and events.phase == "ready" and events.session_id == "s2"
    assert [m.message_id for m in got] == ["in-flight"]


def test_unapproved_reconnect_url_faults(twitch_events_factory: Factory) -> None:
    events, _, statuses = twitch_events_factory(B)  # not mock: only the real Twitch host is allowed
    gen = ready(events)
    assert events.reconnect("wss://evil.example/ws", generation=gen) is False
    assert events.phase == "disconnected" and statuses == ["twitch_disconnected"]


def test_websocket_url_allowlist() -> None:
    assert websocket_url("wss://eventsub.wss.twitch.tv/ws")
    assert websocket_url("wss://eventsub.wss.twitch.tv/ws?challenge=abc")
    assert websocket_url("ws://127.0.0.1:4000/ws", mock=True)
    for bad in (
        "ws://eventsub.wss.twitch.tv/ws",
        "wss://eventsub.wss.twitch.tv.evil.example/ws",
        "wss://user:pw@eventsub.wss.twitch.tv/ws",
        "wss://eventsub.wss.twitch.tv/other",
        "wss://eventsub.wss.twitch.tv/ws#frag",
    ):
        with pytest.raises(ValueError):
            websocket_url(bad)
    with pytest.raises(ValueError):
        websocket_url("wss://eventsub.wss.twitch.tv/ws", mock=True)
    with pytest.raises(ValueError):
        websocket_url("ws://localhost:4000/ws", mock=True)


def test_consumer_failure_is_contained(twitch_events_factory: Factory) -> None:
    def boom(message: ChatMessage) -> None:
        raise RuntimeError(message.text)

    events = ChatEvents(B, boom)
    gen = ready(events)
    assert events.accept(note("secret viewer text"), generation=gen) == "delivery_failed"
