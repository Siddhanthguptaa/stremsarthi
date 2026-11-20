"""Verdict to platform action, with downgrade where the platform cannot or must not act."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from chat.common import ActionResult, ChatPublisher
from chat.moderation import ActionDispatcher
from chat.twitch import TwitchActions
from chat.youtube import QuotaGovernor, YouTubeActions
from contracts import Category, ChatAction, ChatMessage, ChatVerdict, Topic
from contracts.config import ChatConfig, TwitchChatConfig, YoutubeChatConfig
from contracts.fakes import InProcessBus


def message(platform: str = "twitch", mid: str = "m1", **flags: bool) -> ChatMessage:
    return ChatMessage(
        platform=platform,  # type: ignore[arg-type]
        message_id=mid,
        channel_id="1001",
        author_id="2002" if platform == "twitch" else "UCbad0000000000000000001",
        author_display="viewer",
        text="SPECIMEN flagged text",
        received_at=1.0,
        **flags,
    )


def verdict(msg: ChatMessage, action: ChatAction, reason: str = "rules_abuse", secs: int | None = None):  # type: ignore[no-untyped-def]
    if action is ChatAction.TIMEOUT:
        secs = secs or 600
    return ChatVerdict(
        platform=msg.platform,
        message_id=msg.message_id,
        action=action,
        categories=(Category.CHAT_ABUSE,),
        confidence=0.95,
        reason=reason,
        stage="rules",
        latency_ms=1.0,
        timeout_seconds=secs,
    )


class Stub:
    def __init__(self, delete: ActionResult | None = None, timeout: ActionResult | None = None) -> None:
        self.delete_result = delete or ActionResult("done", "ok")
        self.timeout_result = timeout or ActionResult("done", "ok")
        self.calls: list[tuple[str, str]] = []

    async def delete(self, message: ChatMessage) -> ActionResult:
        self.calls.append(("delete", message.message_id))
        return self.delete_result

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult:
        self.calls.append(("timeout", f"{message.message_id}:{seconds}"))
        return self.timeout_result


def cfg(**chat: object) -> ChatConfig:
    return ChatConfig(**chat)  # type: ignore[arg-type]


@pytest.mark.parametrize("action", [ChatAction.ALLOW, ChatAction.HIDE_FROM_STREAMER, ChatAction.HOLD_FOR_MOD])
async def test_local_actions_pass_through_untouched(action: ChatAction) -> None:
    stub = Stub()
    m = message()
    v = (
        verdict(m, action)
        if action is not ChatAction.ALLOW
        else ChatVerdict("twitch", "m1", ChatAction.ALLOW, (), 1.0, "clean", "rules", 0.1)
    )
    out = await ActionDispatcher(twitch=stub, config=cfg()).apply(m, v)
    assert out is v and stub.calls == []


async def test_delete_done_sets_platform_result_and_publishes_followup() -> None:
    bus = InProcessBus()
    stub = Stub()
    m = message()
    d = ActionDispatcher(twitch=stub, config=cfg(), publisher=ChatPublisher(bus))
    out = await d.apply(m, verdict(m, ChatAction.DELETE))
    assert out.action is ChatAction.DELETE and out.platform_result == "done" and out.reason == "rules_abuse"
    assert stub.calls == [("delete", "m1")]
    assert bus.of(Topic.CHAT_VERDICT) == [out]


@pytest.mark.parametrize(
    ("platform", "chat"),
    [
        ("youtube", {"youtube": YoutubeChatConfig(auto_delete=False)}),
        ("twitch", {"twitch": TwitchChatConfig(auto_delete=False)}),
    ],
)
async def test_delete_off_downgrades_to_hold_without_calling_platform(platform: str, chat: dict) -> None:  # type: ignore[type-arg]
    stub = Stub()
    m = message(platform)
    d = ActionDispatcher(**{platform: stub}, config=cfg(**chat))  # type: ignore[arg-type]
    out = await d.apply(m, verdict(m, ChatAction.DELETE, "scam_upi"))
    assert out.action is ChatAction.HOLD_FOR_MOD and out.platform_result == "unsupported"
    assert out.reason == "scam_upi:dg_delete_off_hold" and stub.calls == []


async def test_delete_failure_downgrades_to_hold_with_failed_result() -> None:
    stub = Stub(delete=ActionResult("failed", "quota_low"))
    m = message("youtube")
    out = await ActionDispatcher(youtube=stub, config=cfg()).apply(m, verdict(m, ChatAction.DELETE))
    assert out.action is ChatAction.HOLD_FOR_MOD and out.platform_result == "failed"
    assert out.reason.endswith(":dg_delete_failed_hold") and out.timeout_seconds is None


async def test_unsupported_delete_is_reported_unsupported() -> None:
    stub = Stub(delete=ActionResult("unsupported", "privileged_author"))
    m = message()
    out = await ActionDispatcher(twitch=stub, config=cfg()).apply(m, verdict(m, ChatAction.DELETE))
    assert out.action is ChatAction.HOLD_FOR_MOD and out.platform_result == "unsupported"


async def test_timeout_where_off_downgrades_to_delete_on_youtube_by_default() -> None:
    stub = Stub()
    m = message("youtube")
    out = await ActionDispatcher(youtube=stub, config=cfg()).apply(
        m, verdict(m, ChatAction.TIMEOUT, "repeat_offender")
    )
    assert out.action is ChatAction.DELETE and out.platform_result == "done" and out.timeout_seconds is None
    assert out.reason == "repeat_offender:dg_timeout_off_delete"
    assert stub.calls == [("delete", "m1")]  # the 200-unit ban was never attempted


async def test_timeout_off_and_delete_off_ends_in_hold() -> None:
    stub = Stub()
    m = message("youtube")
    c = cfg(youtube=YoutubeChatConfig(auto_delete=False))
    out = await ActionDispatcher(youtube=stub, config=c).apply(
        m, verdict(m, ChatAction.TIMEOUT, "repeat_offender")
    )
    assert out.action is ChatAction.HOLD_FOR_MOD and out.platform_result == "unsupported"
    assert stub.calls == []


async def test_timeout_on_twitch_runs_and_also_deletes_the_message() -> None:
    stub = Stub()
    m = message()
    out = await ActionDispatcher(twitch=stub, config=cfg()).apply(
        m, verdict(m, ChatAction.TIMEOUT, "repeat_offender")
    )
    assert out.action is ChatAction.TIMEOUT and out.timeout_seconds == 600 and out.platform_result == "done"
    assert stub.calls == [("timeout", "m1:600"), ("delete", "m1")]


async def test_twitch_auto_timeout_false_downgrades() -> None:
    stub = Stub()
    m = message()
    c = cfg(twitch=TwitchChatConfig(auto_timeout=False))
    out = await ActionDispatcher(twitch=stub, config=c).apply(
        m, verdict(m, ChatAction.TIMEOUT, "repeat_offender")
    )
    assert out.action is ChatAction.DELETE and stub.calls == [("delete", "m1")]


async def test_failed_timeout_falls_back_to_delete_then_hold() -> None:
    m = message()
    stub = Stub(timeout=ActionResult("failed", "permissions"))
    out = await ActionDispatcher(twitch=stub, config=cfg()).apply(m, verdict(m, ChatAction.TIMEOUT))
    assert out.action is ChatAction.DELETE and out.reason.endswith(":dg_timeout_failed_delete")
    stub2 = Stub(timeout=ActionResult("failed", "x"), delete=ActionResult("failed", "x"))
    out2 = await ActionDispatcher(twitch=stub2, config=cfg()).apply(m, verdict(m, ChatAction.TIMEOUT))
    assert out2.action is ChatAction.HOLD_FOR_MOD and out2.platform_result == "failed"
    assert out2.reason.endswith(":dg_delete_failed_hold")


async def test_no_connector_means_hold_never_allow() -> None:  # Twitch anonymous read: no actions
    m = message()
    d = ActionDispatcher(twitch=None, config=cfg())
    for action in (ChatAction.DELETE, ChatAction.TIMEOUT):
        out = await d.apply(m, verdict(m, action))
        assert out.action is ChatAction.HOLD_FOR_MOD and out.platform_result == "unsupported"
        assert out.reason.endswith(":dg_no_connector_hold")


async def test_adapter_exception_is_contained_and_reason_stays_a_code() -> None:
    class Bad(Stub):
        async def delete(self, message: ChatMessage) -> ActionResult:
            raise RuntimeError(message.text)

    m = message()
    out = await ActionDispatcher(twitch=Bad(), config=cfg()).apply(m, verdict(m, ChatAction.DELETE, "x" * 64))
    assert out.action is ChatAction.HOLD_FOR_MOD and out.platform_result == "failed"
    assert len(out.reason) <= 64


async def test_manual_moderator_actions_have_no_downgrade() -> None:
    stub = Stub()
    m = message("youtube")
    d = ActionDispatcher(youtube=stub, config=cfg())
    assert (await d.delete(m)).status == "done"
    res = await d.timeout(m, 120)
    assert (res.status, res.reason) == ("unsupported", "timeout_disabled") and stub.calls == [
        ("delete", "m1")
    ]
    d.update_config(cfg(youtube=YoutubeChatConfig(allow_timeout=True)))
    assert (await d.timeout(m, 120)).status == "done"
    assert (await ActionDispatcher(config=cfg()).delete(m)).reason == "no_connector"


async def test_logs_carry_no_message_text(caplog) -> None:  # type: ignore[no-untyped-def]
    m = message()
    with caplog.at_level(logging.DEBUG):
        await ActionDispatcher(twitch=Stub(delete=ActionResult("failed", "x")), config=cfg()).apply(
            m, verdict(m, ChatAction.DELETE)
        )
    assert caplog.records and "SPECIMEN flagged text" not in caplog.text and "viewer" not in caplog.text


# ---- against the fake platform servers --------------------------------------------------------
async def test_youtube_quota_exhaustion_degrades_to_hold_and_hide_only(yt: SimpleNamespace) -> None:
    gov: QuotaGovernor = QuotaGovernor.from_config(
        YoutubeChatConfig(), clock=yt.clock, on_event=yt.statuses.append
    )
    yt.client.governor = gov
    actions = YouTubeActions(yt.client, get_live_chat_id=lambda: yt.fake.live_chat_id, clock=yt.clock)
    d = ActionDispatcher(youtube=actions, config=cfg(), publisher=None)
    results = []
    for _ in range(100):
        mid = yt.fake.add_text("spam")
        m = message("youtube", mid)
        results.append(await d.apply(m, verdict(m, ChatAction.DELETE)))
        yt.clock.advance(1)
    done = [r for r in results if r.platform_result == "done"]
    held = [r for r in results if r.action is ChatAction.HOLD_FOR_MOD]
    assert len(done) == 90 and len(held) == 10  # 2000-unit budget minus the 10 % reserve, 20 per delete
    assert all(r.platform_result == "failed" for r in held)
    assert "yt_quota_low" in yt.statuses and len(yt.fake.deleted) == 90


async def test_youtube_repeat_offender_timeout_default_deletes_via_fake(yt: SimpleNamespace) -> None:
    actions = YouTubeActions(yt.client, get_live_chat_id=lambda: yt.fake.live_chat_id, clock=yt.clock)
    d = ActionDispatcher(youtube=actions, config=cfg())
    mid = yt.fake.add_text("spam")
    m = message("youtube", mid)
    out = await d.apply(m, verdict(m, ChatAction.TIMEOUT, "repeat_offender"))
    assert out.action is ChatAction.DELETE and out.platform_result == "done"
    assert yt.fake.deleted == [mid] and yt.fake.bans == []


async def test_twitch_timeout_and_privileged_author_via_fake(tw: SimpleNamespace) -> None:
    actions = TwitchActions(tw.http, tw.session, tw.fake.user_id, clock=tw.clock)
    d = ActionDispatcher(twitch=actions, config=cfg())
    m = message("twitch", "t1")
    out = await d.apply(m, verdict(m, ChatAction.TIMEOUT, "repeat_offender"))
    assert out.action is ChatAction.TIMEOUT and out.platform_result == "done"
    assert tw.fake.bans[0]["data"]["duration"] == 600 and tw.fake.deleted == ["t1"]
    staff = message("twitch", "t2", is_moderator=True)
    out2 = await d.apply(staff, verdict(staff, ChatAction.DELETE))
    assert out2.action is ChatAction.HOLD_FOR_MOD and out2.platform_result == "unsupported"
    assert tw.fake.deleted == ["t1"]
