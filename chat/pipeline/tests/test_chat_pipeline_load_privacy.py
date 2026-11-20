"""Privacy (case 14), latency probe (case 15), overload (case 16) and the overlay feed rule."""

from __future__ import annotations

import logging
import time

import pytest

from chat.common import SystemClock
from chat.testing.pipeline_kit import make_rig, msg
from chat.testing.synthetic_chat import build_set
from contracts import ChatAction, Topic
from contracts.config import ChatConfig, StreamerPrivateConfig

PRIVATE_TERM = "Specimenpur Colony"
PRIVATE_PHONE = "9876500001"


def flagged_texts(n: int) -> list[str]:
    out: list[str] = []
    for i in range(n):
        tok = f"SPECTOK{i:04d}"
        kind = i % 5
        if kind == 0:
            out.append(f"{tok} tu chutiya harami kutta")
        elif kind == 1:
            out.append(f"{tok} upi://pay?pa=spec{i:04d}@ybl&am=500")
        elif kind == 2:
            out.append(f"{tok} call 98765 {43000 + i}")
        elif kind == 3:
            out.append(f"{tok} he lives in {PRIVATE_TERM.lower()}")
        else:
            out.append(f"{tok} his number is {PRIVATE_PHONE}")
    return out


async def test_no_flagged_text_private_term_or_phone_in_logs_events_or_reprs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = ChatConfig(streamer_private=StreamerPrivateConfig(terms=[PRIVATE_TERM], phones=[PRIVATE_PHONE]))
    rig = make_rig(cfg)
    texts = flagged_texts(200)
    with caplog.at_level(logging.DEBUG):
        for i, t in enumerate(texts):
            rig.pipe.submit(msg(t, i + 1, "twitch" if i % 2 else "youtube", author=f"UCauthor{i:04d}"))
        await rig.settle()
        await rig.pipe.stop()
    assert rig.allowed_ids() == []  # every one of them was flagged
    assert len(rig.of(Topic.CHAT_VERDICT)) >= 200
    log_blob = caplog.text + "\n".join(str(r.__dict__.get("exc_info")) for r in caplog.records)
    assert len(caplog.records) > 200  # the check is meaningful: lines were produced
    for i, t in enumerate(texts):
        assert f"SPECTOK{i:04d}" not in log_blob and t not in log_blob
    for secret in (PRIVATE_TERM, PRIVATE_TERM.lower(), PRIVATE_PHONE, "98765 43", "spec00", "UCauthor"):
        assert secret not in log_blob
    # bus events other than the moderator-only ones carry no text either
    wire = repr(rig.of(Topic.CHAT_VERDICT)) + repr(rig.of(Topic.SAFETY_EVENT)) + repr(rig.pipe.stats())
    assert "SPECTOK" not in wire and PRIVATE_TERM not in wire and PRIVATE_PHONE not in wire
    assert "SPECTOK" not in repr(rig.pipe) + repr(rig.pipe.modqueue) + repr(rig.pipe.router) + repr(
        rig.pipe.feed
    )


async def test_overload_burst_is_bounded_and_hold_command_is_instant() -> None:
    rig = make_rig()
    n = 5000
    t0 = time.perf_counter()
    for i in range(n):  # a tight loop: the worker gets no turn, the queue must stay bounded
        rig.pipe.submit(msg(f"bhai kya gameplay hai {i}", i + 1))
        assert len(rig.pipe._queue) <= 1000
    submit_s = time.perf_counter() - t0
    assert rig.pipe.counts["dropped_overload"] == n - 1000
    t1 = time.perf_counter()
    rig.pipe.submit(msg("!hold", n + 1, broadcaster=True))
    hold_ms = (time.perf_counter() - t1) * 1000
    assert len(rig.of(Topic.HOLD)) == 1 and hold_ms < 100
    assert 1 <= len(rig.safety("chat_overload")) <= 2  # rate limited, not one per drop
    await rig.settle(15)
    assert len(rig.allowed_ids()) == 1000  # only what was queued is shown; dropped text is never released
    assert submit_s < 10
    await rig.pipe.stop()


async def test_latency_probe_rules_and_provisional_path() -> None:
    rig = make_rig(clock=SystemClock())
    samples = build_set()[:1000]
    for k in range(0, len(samples), 20):  # paced like a busy chat: 20 messages per loop turn
        for j, s in enumerate(samples[k : k + 20]):
            rig.pipe.submit(msg(s.text, k + j + 1, "twitch" if j % 2 else "youtube", author=f"a{k + j}"))
        await rig.pipe.idle(5)
    await rig.settle(10)
    p95 = rig.pipe.latency_p95_ms()
    assert len(rig.pipe.latency_ms) >= 900 and p95 <= 300.0  # spec: p95 <= 300 ms (target <= 20 ms)
    print(f"pipeline submit-to-verdict p95 = {p95:.2f} ms over {len(rig.pipe.latency_ms)} messages")
    await rig.pipe.stop()


async def test_overlay_feed_rule_text_only_for_allowed() -> None:
    rig = make_rig()
    cases = {1: "bhai kya gameplay hai", 2: "tu chutiya harami kutta", 3: "upi://pay?pa=x1@ybl&am=5"}
    for i, t in cases.items():
        rig.pipe.submit(msg(t, i))
    await rig.settle()
    assert rig.allowed_ids() == ["m1"]
    state = rig.pipe.feed.state()
    assert state.shown == 1 and state.hidden_count == 2
    hidden_by_verdict = {v.message_id for v in rig.of(Topic.CHAT_VERDICT) if v.action is not ChatAction.ALLOW}
    assert hidden_by_verdict == {"m2", "m3"}  # a consumer derives the same count from chat.verdict
    await rig.pipe.stop()
