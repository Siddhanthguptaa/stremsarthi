"""Cascade routing (case 6), fake-UPI verdict (case 4), action mapping, staff messages, repeat offenders."""

from __future__ import annotations

import asyncio

import pytest

from chat.classify import ChatClassifier
from chat.testing.fake_llm import FakeLlm, ok, reply
from contracts import Category, ChatAction, ChatMessage, ChatVerdict
from contracts.config import ChatActionsConfig, ChatConfig, ClassifyConfig, StreamerPrivateConfig


def msg(text: str, i: int = 1, **kw) -> ChatMessage:
    kw.setdefault("author_id", f"author{i}")
    return ChatMessage("youtube", f"m{i}", "chan", kw.pop("author_id"), "viewer", text, 1_700_000_000.0, **kw)


def cfg(**classify) -> ChatConfig:
    base = {"llm_flush_ms": 5, "llm_pending_max_s": 1.0, "llm_timeout_ms": 200}
    base.update(classify)
    return ChatConfig(classify=ClassifyConfig(**base))


def fast(clf: ChatClassifier, text: str, i: int = 1, **kw) -> ChatVerdict:
    return clf.classify_fast(msg(text, i, **kw)).verdict


def test_clear_clean_is_allowed_fast() -> None:
    clf = ChatClassifier(cfg())
    v = fast(clf, "bhai kya gameplay hai")
    assert v.action is ChatAction.ALLOW and v.stage == "rules" and v.reason == "clean"
    assert v.latency_ms <= 5.0


def test_fake_upi_link_is_deleted_with_high_confidence() -> None:
    v = fast(ChatClassifier(cfg()), "upi://pay?pa=specimen@ybl&am=500")
    assert v.action is ChatAction.DELETE and v.categories == (Category.CHAT_FAKE_UPI,)
    assert v.confidence >= 0.98 and v.reason == "fake_upi_link" and v.stage == "rules"


def test_default_action_mapping() -> None:
    clf = ChatClassifier(cfg())
    assert (
        fast(clf, "tu chutiya harami kutta", 1).action is ChatAction.HIDE_FROM_STREAMER
    )  # abuse -> hide (0.9 final)
    severe = fast(clf, "madarchod", 2)
    assert severe.action is ChatAction.TIMEOUT and severe.timeout_seconds == 600  # severe_abuse default
    assert fast(clf, "call 9876501234", 3).action is ChatAction.DELETE  # doxxing
    assert fast(clf, "free diamonds bit.ly/x", 4).action is ChatAction.DELETE  # scam


def test_actions_follow_config() -> None:
    config = ChatConfig(actions=ChatActionsConfig(severe_abuse="delete", doxxing="hold_for_mod", scam="hide"))
    clf = ChatClassifier(config)
    assert fast(clf, "madarchod", 1).action is ChatAction.DELETE
    assert fast(clf, "call 9876501234", 2).action is ChatAction.HOLD_FOR_MOD
    assert fast(clf, "upi://pay?pa=a@ybl", 3).action is ChatAction.HIDE_FROM_STREAMER


def test_categories_cover_every_finding() -> None:
    v = fast(ChatClassifier(cfg()), "madarchod call 9876501234 upi://pay?pa=a@ybl")
    assert set(v.categories) == {Category.CHAT_ABUSE, Category.CHAT_DOXXING, Category.CHAT_FAKE_UPI}
    assert v.categories[0] is Category.CHAT_FAKE_UPI  # highest score first


def test_command_messages_are_allowed_silently() -> None:
    clf = ChatClassifier(cfg())
    for t in ["!hold", " !UNHOLD ", "!Hold"]:
        v = fast(clf, t)
        assert v.action is ChatAction.ALLOW and v.reason == "command"


def test_empty_and_emoji_only_messages_are_allowed() -> None:
    clf = ChatClassifier(cfg())
    for t in ["", "   ", "😂😂😂", "🔥"]:
        assert fast(clf, t).action is ChatAction.ALLOW


def test_very_long_messages_are_handled() -> None:
    v = fast(ChatClassifier(cfg()), "nice " * 5000 + "madarchod")
    assert v.action is not ChatAction.ALLOW or v.reason == "clean"  # truncated at 2000 chars: must not crash


# --- no LLM: local rules fallback -------------------------------------------------------------------


def test_borderline_without_llm_is_held_never_silently_allowed() -> None:
    clf = ChatClassifier(cfg())  # no transport
    v = fast(clf, "bc what a play")
    assert v.action is ChatAction.HOLD_FOR_MOD and v.reason == "llm_unavailable_hold"
    assert Category.CHAT_ABUSE in v.categories


def test_llm_disabled_in_config_uses_rules_fallback() -> None:
    clf = ChatClassifier(cfg(llm_enabled=False), llm=FakeLlm())
    assert clf.llm_stage is None
    assert fast(clf, "saale").action is ChatAction.HOLD_FOR_MOD


# --- with LLM: provisional then final ---------------------------------------------------------------


async def test_borderline_gets_provisional_hide_then_llm_upgrade() -> None:
    llm = FakeLlm(default=lambda texts: reply([ok(i, ["abuse"], 0.6, "streamer") for i in range(len(texts))]))
    finals: list[tuple[ChatMessage, ChatVerdict]] = []
    done = asyncio.Event()
    clf = ChatClassifier(cfg(), llm=llm, on_update=lambda m, v: (finals.append((m, v)), done.set()))
    [prov] = await clf.classify([msg("bc what a play")])
    assert (
        prov.action is ChatAction.HIDE_FROM_STREAMER
        and prov.reason == "pending_llm"
        and prov.stage == "rules"
    )
    await asyncio.wait_for(done.wait(), 2)
    ((m, final),) = finals
    assert m.message_id == "m1"
    assert (
        final.action is ChatAction.HOLD_FOR_MOD
        and final.stage == "llm"
        and final.categories == (Category.CHAT_ABUSE,)
    )
    await clf.aclose()


async def test_llm_confident_flag_uses_configured_action() -> None:
    llm = FakeLlm(default=lambda texts: reply([ok(i, ["scam"], 0.95) for i in range(len(texts))]))
    clf = ChatClassifier(cfg(), llm=llm)
    [v] = await clf.classify_final([msg("bit.ly/specimen")])  # bare shortener: borderline 0.35
    assert v.action is ChatAction.DELETE and v.categories == (Category.CHAT_SCAM_LINK,) and v.stage == "llm"
    await clf.aclose()


async def test_borderline_llm_downgrade_to_allow_is_reported() -> None:
    finals: list[ChatVerdict] = []
    done = asyncio.Event()
    clf = ChatClassifier(cfg(), llm=FakeLlm(), on_update=lambda m, v: (finals.append(v), done.set()))
    [prov] = await clf.classify([msg("teri maa ki kasam bhai op")])
    assert prov.action is ChatAction.HIDE_FROM_STREAMER
    await asyncio.wait_for(done.wait(), 2)
    assert (
        finals[0].action is ChatAction.ALLOW and finals[0].reason == "llm_clean" and finals[0].stage == "llm"
    )
    await clf.aclose()


async def test_clear_cases_never_call_the_llm() -> None:
    llm = FakeLlm()
    clf = ChatClassifier(cfg(), llm=llm)
    vs = await clf.classify_final([msg("hello bhai", 1), msg("madarchod", 2), msg("upi://pay?pa=a@ybl", 3)])
    assert [v.stage for v in vs] == ["rules"] * 3 and llm.calls == []
    await clf.aclose()


async def test_llm_unavailable_finalises_to_hold_for_mod() -> None:
    llm = FakeLlm(default=lambda texts: "not json")
    clf = ChatClassifier(cfg(llm_pending_max_s=0.3), llm=llm)
    [v] = await clf.classify_final([msg("bc what a play")])
    assert v.action is ChatAction.HOLD_FOR_MOD and v.reason == "llm_timeout_hold"
    assert len(llm.calls) == 2  # tried once, retried once
    await clf.aclose()


async def test_callback_errors_never_escape() -> None:
    def boom(m: ChatMessage, v: ChatVerdict) -> None:
        raise RuntimeError("callback failure")

    clf = ChatClassifier(cfg(), llm=FakeLlm(), on_update=boom)
    await clf.classify([msg("bc what a play")])
    await asyncio.sleep(0.1)
    await clf.aclose()


# --- staff, repeat offenders, config ----------------------------------------------------------------


def test_staff_are_never_hidden_but_dox_is_flagged_for_mods() -> None:
    clf = ChatClassifier(ChatConfig(streamer_private=StreamerPrivateConfig(terms=["zzqq secret"], phones=[])))
    assert fast(clf, "madarchod", 1, is_moderator=True).action is ChatAction.ALLOW
    assert fast(clf, "free diamonds bit.ly/x", 2, is_broadcaster=True).action is ChatAction.ALLOW
    assert (
        fast(clf, "zzqq secret is my name", 3, is_broadcaster=True).action is ChatAction.ALLOW
    )  # own private data
    v = fast(clf, "his number is 9876501234", 4, is_moderator=True)
    assert (
        v.action is ChatAction.ALLOW
        and v.categories == (Category.CHAT_DOXXING,)
        and v.reason == "staff_dox_flag"
    )


def test_repeat_offender_is_timed_out_on_third_flag() -> None:
    clf = ChatClassifier(ChatConfig(actions=ChatActionsConfig(abuse="hide", severe_abuse="hide")))
    actions = [fast(clf, "chutiya harami kutta", i, author_id="same").action for i in (1, 2, 3)]
    assert actions[:2] == [ChatAction.HIDE_FROM_STREAMER] * 2 and actions[2] is ChatAction.TIMEOUT
    v = fast(clf, "chutiya harami kutta", 4, author_id="same")
    assert v.reason == "repeat_offender" and v.timeout_seconds == 600
    other = fast(clf, "chutiya harami kutta", 5, author_id="other")
    assert other.action is ChatAction.HIDE_FROM_STREAMER


def test_update_config_applies_new_private_terms() -> None:
    clf = ChatClassifier(ChatConfig())
    assert fast(clf, "zzqq secret ka ghar", 1).action is ChatAction.ALLOW
    clf.update_config(ChatConfig(streamer_private=StreamerPrivateConfig(terms=["zzqq secret"], phones=[])))
    v = fast(clf, "zzqq secret ka ghar", 2)
    assert v.action is ChatAction.DELETE and v.reason == "dox_private_term"


async def test_classify_satisfies_the_contract_protocol() -> None:
    from contracts import ChatClassifier as Proto

    clf = ChatClassifier(ChatConfig())
    assert isinstance(clf, Proto)
    vs = await clf.classify([msg("hello", 1), msg("madarchod", 2)])
    assert len(vs) == 2 and [v.message_id for v in vs] == ["m1", "m2"]
    with pytest.raises(ValueError):
        ChatVerdict(
            "youtube", "x", ChatAction.DELETE, (), 0.9, "r", "rules", 1.0
        )  # non-ALLOW needs a category
