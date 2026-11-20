"""Orchestrator flow: commands first, routing per verdict, late LLM verdicts, staff flags, config, mod actions."""

from __future__ import annotations

from chat.common import SystemClock
from chat.testing import until
from chat.testing.fake_llm import FakeLlm, ok, reply
from chat.testing.pipeline_kit import (
    ABUSE,
    BORDERLINE,
    CLEAN,
    FAKE_UPI,
    PHONE,
    SEVERE,
    make_rig,
    mod_action,
    msg,
)
from contracts import Category, ChatAction, ConfigChange, HoldSource, Topic
from contracts.config import ChatConfig, ClassifyConfig


def llm_cfg() -> ChatConfig:
    return ChatConfig(classify=ClassifyConfig(llm_flush_ms=5, llm_pending_max_s=1.0, llm_timeout_ms=200))


async def test_clean_message_is_allowed_and_shown_once() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(CLEAN, 1))
    rig.pipe.submit(msg(CLEAN, 1))  # redelivery of the same id
    await rig.settle()
    assert rig.allowed_ids() == ["m1"] and rig.pipe.counts["duplicates"] == 1
    [v] = rig.verdicts("m1")
    assert v.action is ChatAction.ALLOW and rig.of(Topic.CHAT_MODQUEUE) == []
    assert rig.pipe.feed.hidden_count == 0
    await rig.pipe.stop()


async def test_hold_command_runs_before_classifier_and_is_silent() -> None:
    rig = make_rig()
    rig.pipe.submit(msg("!hold", 1, moderator=True))
    # synchronous: the HoldCommand is on the bus before the worker ever runs
    [hold] = rig.of(Topic.HOLD)
    assert hold.source is HoldSource.CHAT and hold.active is True
    await rig.settle()
    assert rig.allowed_ids() == [] and rig.of(Topic.CHAT_VERDICT) == [] and rig.of(Topic.CHAT_MODQUEUE) == []
    assert rig.pipe.counts["commands"] == 1
    await rig.pipe.stop()


async def test_denied_command_is_silent_and_reports_cmd_denied() -> None:
    rig = make_rig()
    rig.pipe.submit(msg("!hold", 1))  # plain viewer
    rig.pipe.submit(msg("!unhold", 2, moderator=True))  # unhold is broadcaster only
    await rig.settle()
    assert rig.of(Topic.HOLD) == []
    assert rig.allowed_ids() == [] and rig.of(Topic.CHAT_VERDICT) == [] and rig.of(Topic.CHAT_MODQUEUE) == []
    assert len(rig.safety("cmd_denied")) >= 1
    await rig.pipe.stop()


async def test_abuse_is_hidden_queued_and_reported() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(ABUSE, 1))
    await rig.settle()
    assert rig.allowed_ids() == []
    [v] = rig.verdicts("m1")
    assert v.action is ChatAction.HIDE_FROM_STREAMER
    assert rig.queue_item("m1").state == "pending"
    [ev] = rig.safety("rules_abuse")
    assert ev.action == "hidden" and ev.category is Category.CHAT_ABUSE
    assert rig.pipe.feed.hidden_count == 1
    await rig.pipe.stop()


async def test_fake_upi_is_deleted_on_platform_and_queued_as_deleted() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(FAKE_UPI, 1))
    await rig.settle()
    assert rig.twitch.calls == [("delete", "m1")] and rig.allowed_ids() == []
    done = [v for v in rig.verdicts("m1") if v.platform_result == "done"]
    assert done and done[-1].action is ChatAction.DELETE  # follow-up verdict carries the result
    assert rig.queue_item("m1").state == "deleted"
    assert [e.action for e in rig.safety()] == ["deleted"]
    await rig.pipe.stop()


async def test_delete_without_connector_downgrades_to_hold_never_allow() -> None:
    rig = make_rig()
    rig.pipe.dispatcher._backends["twitch"] = None  # anonymous IRC: no actions
    rig.pipe.submit(msg(PHONE, 1))
    await rig.settle()
    item = rig.queue_item("m1")
    assert item.verdict.action is ChatAction.HOLD_FOR_MOD and item.state == "pending"
    assert item.verdict.platform_result == "unsupported" and rig.allowed_ids() == []
    assert rig.safety()[0].action == "held"
    await rig.pipe.stop()


async def test_severe_abuse_timeout_on_twitch_and_delete_fallback_on_youtube() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(SEVERE, 1, "twitch"))
    rig.pipe.submit(msg(SEVERE, 2, "youtube"))  # YouTube timeouts are off by default
    await rig.settle()
    assert (
        rig.twitch.calls[0][0] == "timeout" and rig.twitch.calls[-1][0] == "delete"
    )  # timeout then its delete
    assert rig.youtube.calls == [("delete", "m2")]
    assert rig.queue_item("m1").state == "timed_out" and rig.queue_item("m2").state == "deleted"
    assert rig.queue_item("m2").verdict.reason.endswith(":dg_timeout_off_delete")
    await rig.pipe.stop()


async def test_provisional_hide_then_llm_upgrade_applies_the_platform_action() -> None:
    llm = FakeLlm(default=lambda t: reply([ok(i, ["scam"], 0.95) for i in range(len(t))]))
    rig = make_rig(llm_cfg(), llm=llm, clock=SystemClock())
    rig.pipe.submit(msg("bit.ly/specimen", 1))  # bare shortener: borderline
    await until(lambda: bool(rig.verdicts("m1")))
    assert rig.verdicts("m1")[0].reason == "pending_llm" and rig.of(Topic.CHAT_MODQUEUE) == []
    await until(lambda: rig.queue_item("m1") is not None)
    await rig.settle()
    assert rig.twitch.calls == [("delete", "m1")]  # late verdict still reaches ActionDispatcher.apply
    assert rig.queue_item("m1").state == "deleted" and rig.allowed_ids() == []
    assert rig.pipe.feed.hidden_count == 1
    await rig.pipe.stop()


async def test_provisional_hide_then_llm_downgrade_publishes_chat_allowed() -> None:
    rig = make_rig(llm_cfg(), llm=FakeLlm(), clock=SystemClock())  # FakeLlm says clean
    rig.pipe.submit(msg(BORDERLINE, 1))
    await until(lambda: bool(rig.verdicts("m1")))
    assert rig.pipe.feed.hidden_count == 1 and rig.allowed_ids() == []  # hidden while pending
    await until(lambda: rig.allowed_ids() == ["m1"])
    await rig.settle()
    assert [v.reason for v in rig.verdicts("m1")] == ["pending_llm", "llm_clean"]
    assert rig.pipe.feed.hidden_count == 0 and rig.of(Topic.CHAT_MODQUEUE) == []
    assert rig.safety() == []  # a provisional hide that was cleared leaves no safety event
    await rig.pipe.stop()


async def test_borderline_without_llm_is_held_for_moderators() -> None:
    rig = make_rig()  # no transport
    rig.pipe.submit(msg(BORDERLINE, 1))
    await rig.settle()
    assert rig.queue_item("m1").verdict.action is ChatAction.HOLD_FOR_MOD and rig.allowed_ids() == []
    assert rig.safety()[0].action == "held"
    await rig.pipe.stop()


async def test_staff_dox_flag_is_shown_and_queued_for_moderators() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(PHONE, 1, moderator=True))
    await rig.settle()
    assert rig.allowed_ids() == ["m1"]  # never hidden
    item = rig.queue_item("m1")
    assert item.state == "pending" and item.verdict.reason == "staff_dox_flag"
    assert item.verdict.action is ChatAction.ALLOW and Category.CHAT_DOXXING in item.verdict.categories
    assert rig.twitch.calls == [] and rig.safety()[0].action == "warned"
    assert rig.pipe.feed.hidden_count == 0
    await rig.pipe.stop()


async def test_mod_action_over_the_bus_approve_releases_to_overlay() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(ABUSE, 1))
    await rig.settle()
    rig.bus.publish(Topic.CHAT_MODACTION, mod_action(1, "approve"))
    await until(lambda: rig.allowed_ids() == ["m1"])
    await until(lambda: rig.queue_item("m1").state == "approved")
    assert rig.pipe.feed.hidden_count == 0  # approval takes it back out of the hidden count
    await rig.pipe.stop()


async def test_mod_action_delete_over_the_bus_uses_platform() -> None:
    rig = make_rig()
    rig.pipe.submit(msg(ABUSE, 1))
    await rig.settle()
    rig.bus.publish(Topic.CHAT_MODACTION, mod_action(1, "delete"))
    await until(lambda: rig.queue_item("m1").state == "deleted")
    assert rig.twitch.calls == [("delete", "m1")] and rig.allowed_ids() == []
    await rig.pipe.stop()


async def test_config_change_updates_actions_and_youtube_timeout_gate() -> None:
    rig = make_rig()
    assert rig.pipe.apply_config(ConfigChange({"chat.actions.abuse": "delete"}, "a")) is True
    rig.pipe.submit(msg(ABUSE, 1))
    await rig.settle()
    assert rig.twitch.calls == [("delete", "m1")]
    rig.bus.publish(Topic.CONFIG, ConfigChange({"chat.youtube.allow_timeout": True}, "a"))
    await until(lambda: rig.youtube.allow_timeout is True)
    assert rig.pipe.dispatcher.policy("youtube").allow_timeout is True
    await rig.pipe.stop()


async def test_config_change_streamer_private_terms_reach_the_classifier() -> None:
    rig = make_rig()
    rig.pipe.apply_config(ConfigChange({"chat.streamer_private.terms": ["Specimenpur Colony"]}, "a"))
    rig.pipe.submit(msg("he lives in specimenpur colony", 1))
    await rig.settle()
    assert rig.queue_item("m1").verdict.categories == (Category.CHAT_DOXXING,)
    await rig.pipe.stop()


async def test_invalid_or_foreign_config_change_is_ignored() -> None:
    rig = make_rig()
    before = rig.pipe.config
    assert rig.pipe.apply_config(ConfigChange({"categories.face.enabled": False}, "a")) is False
    assert rig.pipe.apply_config(ConfigChange({"chat.no_such_key": 1}, "a")) is False
    assert rig.pipe.apply_config(ConfigChange({"chat.classify.allow_below": 7.0}, "a")) is False
    assert rig.pipe.config is before and rig.pipe.counts["config_rejected"] == 2
    await rig.pipe.stop()


async def test_classifier_failure_fails_closed_and_pipeline_survives() -> None:
    class Boom:
        calls = 0

        async def classify(self, batch):  # type: ignore[no-untyped-def]
            Boom.calls += 1
            if Boom.calls == 1:
                raise RuntimeError("SPECIMEN secret text must not leak")
            return await good.classify(batch)

    from contracts.fakes import FakeChatClassifier

    good = FakeChatClassifier()
    rig = make_rig(classifier=Boom())
    rig.pipe.submit(msg(CLEAN, 1))
    await rig.settle()
    assert rig.allowed_ids() == [] and rig.pipe.counts["classify_errors"] == 1
    assert len(rig.safety("chat_classify_error")) == 1
    rig.pipe.submit(msg(CLEAN, 2))
    await rig.settle()
    assert rig.allowed_ids() == ["m2"]
    await rig.pipe.stop()
