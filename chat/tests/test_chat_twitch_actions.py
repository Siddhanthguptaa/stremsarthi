"""Helix delete and timeout against the fake Twitch server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat.twitch import TwitchActions
from contracts import ChatMessage


def msg(message_id: str = "m1", **kwargs: object) -> ChatMessage:
    base: dict[str, object] = {
        "platform": "twitch",
        "message_id": message_id,
        "channel_id": "1001",
        "author_id": "2002",
        "author_display": "viewer",
        "text": "spam",
        "received_at": 1.0,
    }
    base.update(kwargs)
    return ChatMessage(**base)  # type: ignore[arg-type]


def make(tw: SimpleNamespace) -> tuple[TwitchActions, list[str]]:
    statuses: list[str] = []
    return TwitchActions(
        tw.http, tw.session, tw.fake.user_id, clock=tw.clock, on_status=statuses.append
    ), statuses


async def test_delete_uses_moderator_id_of_the_token_user(tw: SimpleNamespace) -> None:
    actions, _ = make(tw)
    result = await actions.delete(msg("abc-123"))
    assert (result.status, result.reason) == ("done", "ok")
    assert tw.fake.deleted == ["abc-123"]
    assert tw.fake.delete_queries[0] == {
        "broadcaster_id": tw.fake.user_id,
        "moderator_id": tw.fake.user_id,
        "message_id": "abc-123",
    }
    last = tw.fake.helix_headers[-1]
    assert last["Client-Id"] == tw.fake.client_id and last["Authorization"].startswith("Bearer ")


async def test_timeout_posts_a_ban_with_duration_and_fixed_reason(tw: SimpleNamespace) -> None:
    actions, _ = make(tw)
    assert (await actions.timeout(msg(), 600)).status == "done"
    (ban,) = tw.fake.bans
    assert ban["query"] == {"broadcaster_id": tw.fake.user_id, "moderator_id": tw.fake.user_id}
    assert ban["data"] == {"user_id": "2002", "duration": 600, "reason": "stream_sarthi"}  # no message text
    for bad in (0, -5, 1_209_601):
        with pytest.raises(ValueError):
            await actions.timeout(msg(), bad)
    assert (await actions.timeout(msg(), 1_209_600)).status == "done"


async def test_privileged_and_foreign_messages_are_never_moderated(tw: SimpleNamespace) -> None:
    actions, _ = make(tw)
    assert (await actions.delete(msg(is_moderator=True))).reason == "privileged_author"
    assert (await actions.timeout(msg(is_broadcaster=True), 60)).status == "unsupported"
    assert (await actions.delete(msg(platform="youtube"))).reason == "wrong_platform"
    assert tw.fake.deleted == [] and tw.fake.bans == []


async def test_not_a_moderator_blocks_actions_once(tw: SimpleNamespace) -> None:
    tw.fake.fail_next_helix(403)
    actions, statuses = make(tw)
    assert (await actions.delete(msg())).reason == "permissions"
    assert (await actions.delete(msg("m2"))).reason == "actions_stopped"
    assert statuses == ["twitch_actions_blocked"] and tw.fake.deleted == []


async def test_rate_limit_uses_reset_header_then_recovers(tw: SimpleNamespace) -> None:
    tw.fake.fail_next_helix(429, {"Ratelimit-Reset": str(int(tw.clock.wall()) + 5)})
    actions, _ = make(tw)
    assert (await actions.delete(msg())).reason == "rate_limited"
    assert (await actions.delete(msg())).reason == "rate_limited"  # cooling down: no request
    assert tw.fake.deleted == []
    tw.clock.advance(6)
    assert (await actions.delete(msg())).status == "done"


async def test_other_failures_do_not_block(tw: SimpleNamespace) -> None:
    actions, statuses = make(tw)
    tw.fake.fail_next_helix(404)
    assert (await actions.delete(msg())).reason == "gone"
    tw.fake.fail_next_helix(400)
    assert (await actions.delete(msg())).reason == "rejected"
    tw.fake.fail_next_helix(502)
    assert (await actions.delete(msg())).reason == "server_error"
    assert (await actions.delete(msg())).status == "done" and statuses == []


async def test_revoked_token_blocks_actions(tw: SimpleNamespace) -> None:
    actions, statuses = make(tw)
    tw.clock.advance(3601)
    tw.fake.valid_tokens.clear()
    assert (await actions.delete(msg())).reason == "auth"
    assert statuses == ["twitch_actions_blocked"]


async def test_transport_failure_is_a_fixed_code(tw: SimpleNamespace) -> None:
    from chat.twitch import make_http

    dead = make_http(tw.aio, loopback_origin="http://127.0.0.1:1")
    actions = TwitchActions(dead, tw.session, tw.fake.user_id, clock=tw.clock)
    assert (await actions.delete(msg())).reason == "http_failed"
