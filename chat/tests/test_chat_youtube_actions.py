"""YouTube delete / timeout actions, quota accounting and degradation to hide-only."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat.youtube import IdMap, YouTubeActions
from chat.youtube.normalize import normalize_item
from contracts import ChatMessage


def msg(message_id: str, **kwargs: object) -> ChatMessage:
    base: dict[str, object] = {
        "platform": "youtube",
        "message_id": message_id,
        "channel_id": "LIVECHAT_SPECIMEN_1",
        "author_id": "UCbad0000000000000000001",
        "author_display": "someone",
        "text": "spam spam",
        "received_at": 1.0,
    }
    base.update(kwargs)
    return ChatMessage(**base)  # type: ignore[arg-type]


def actions(yt: SimpleNamespace, **kwargs: object) -> tuple[YouTubeActions, list[str]]:
    statuses: list[str] = []
    obj = YouTubeActions(
        yt.client,
        get_live_chat_id=lambda: yt.fake.live_chat_id,
        clock=yt.clock,
        on_status=statuses.append,
        **kwargs,  # type: ignore[arg-type]
    )
    return obj, statuses


async def test_delete_costs_20_units_and_reports_done(yt: SimpleNamespace) -> None:
    mid = yt.fake.add_text("spam")
    act, _ = actions(yt)
    result = await act.delete(msg(mid))
    assert (result.status, result.reason) == ("done", "ok")
    assert yt.fake.deleted == [mid] and yt.fake.units_used == 20 and yt.governor.used_action == 20


async def test_broadcaster_and_moderator_messages_are_never_touched(yt: SimpleNamespace) -> None:
    act, _ = actions(yt, allow_timeout=True)
    for flags in ({"is_moderator": True}, {"is_broadcaster": True}):
        assert (await act.delete(msg("x", **flags))).status == "unsupported"
        assert (await act.timeout(msg("x", **flags), 60)).status == "unsupported"
    assert yt.fake.requests == [] or all(r.method != "DELETE" for r in yt.fake.requests)


async def test_timeout_is_off_by_default_and_uses_a_temporary_ban(yt: SimpleNamespace) -> None:
    act, _ = actions(yt)
    assert (await act.timeout(msg("m"), 600)).status == "unsupported"
    assert yt.fake.bans == []
    act_on, _ = actions(yt, allow_timeout=True)
    result = await act_on.timeout(msg("m"), 600)
    assert result.status == "done" and yt.fake.units_used == 200
    ban = yt.fake.bans[0]
    assert ban["type"] == "temporary" and ban["banDurationSeconds"] == 600
    assert ban["liveChatId"] == yt.fake.live_chat_id
    assert ban["bannedUserDetails"] == {"channelId": "UCbad0000000000000000001"}
    with pytest.raises(ValueError):
        await act_on.timeout(msg("m"), 0)


async def test_actions_degrade_to_hide_only_before_the_budget_is_gone(yt: SimpleNamespace) -> None:
    ids = [yt.fake.add_text(f"s{i}") for i in range(120)]
    act, _ = actions(yt)
    results = [await act.delete(msg(i)) for i in ids]
    done = sum(r.status == "done" for r in results)
    assert done == 90  # the 10 % reserve of the 2,000-unit action budget is kept
    assert all(r.status == "failed" and r.reason == "quota_low" for r in results[90:])
    assert len(yt.fake.deleted) == 90  # nothing was even sent after the budget ran low
    assert yt.statuses == ["yt_quota_low"]
    assert yt.governor.reads_enabled  # reading continues: read-only mode
    assert yt.fake.units_used == 1800 and yt.fake.quota_denied == 0


async def test_server_side_quota_exceeded_stops_actions_and_reads(yt: SimpleNamespace) -> None:
    mid = yt.fake.add_text("spam")
    yt.fake.daily_quota = 10  # Google says the day is spent, although our counter disagreed
    act, statuses = actions(yt)
    result = await act.delete(msg(mid))
    assert (result.status, result.reason) == ("failed", "quota")
    assert statuses == ["yt_actions_blocked"] and act.blocked_reason == "quota"
    assert not yt.governor.reads_enabled
    again = await act.delete(msg(mid))
    assert again.reason == "actions_stopped"
    assert yt.fake.quota_denied == 1  # exactly one call hit the wall: no hammering


async def test_missing_permission_blocks_actions_once(yt: SimpleNamespace) -> None:
    mid = yt.fake.add_text("spam")
    yt.fake.inject(403, reason="insufficientPermissions", path="/youtube/v3/liveChat/messages")
    act, statuses = actions(yt)
    assert (await act.delete(msg(mid))).reason == "permissions"
    assert (await act.delete(msg(mid))).reason == "actions_stopped"
    assert statuses == ["yt_actions_blocked"]


async def test_gone_message_fails_without_blocking(yt: SimpleNamespace) -> None:
    act, statuses = actions(yt)
    assert (await act.delete(msg("not-there"))).reason == "gone"
    assert act.blocked_reason is None and statuses == []


async def test_rate_limit_cools_down_then_recovers(yt: SimpleNamespace) -> None:
    mid = yt.fake.add_text("spam")
    yt.fake.inject(403, reason="rateLimitExceeded", path="/youtube/v3/liveChat/messages")
    act, _ = actions(yt)
    assert (await act.delete(msg(mid))).reason == "rate_limited"
    assert (await act.delete(msg(mid))).reason == "rate_limited"  # cooling down, no call made
    yt.clock.advance(31)
    assert (await act.delete(msg(mid))).status == "done"


async def test_ids_that_break_the_contract_pattern_round_trip_through_a_hash(yt: SimpleNamespace) -> None:
    ids = IdMap()
    odd = "LCC.weird=id/with+chars"
    yt.fake.add_text("spam", message_id=odd)
    item = yt.fake.items[-1]
    message = normalize_item(item, live_chat_id=yt.fake.live_chat_id, received_at=1.0, ids=ids)
    assert message is not None and message.message_id.startswith("h:") and "=" not in message.message_id
    act, _ = actions(yt, ids=ids)
    assert (await act.delete(message)).status == "done"
    assert yt.fake.deleted == [odd]  # the platform got its original id back
