"""Privacy (case 14, classifier part), latency (case 15) and the eval-set acceptance numbers."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time

import pytest

from chat.classify import ChatClassifier
from chat.classify.bench import bench_config, make_messages, percentile, score
from chat.testing.fake_llm import FakeLlm, ok, reply
from chat.testing.synthetic_chat import build_set
from contracts import ChatAction, ChatMessage
from contracts.config import ChatConfig, ClassifyConfig, StreamerPrivateConfig


def _msg(text: str, i: int) -> ChatMessage:
    return ChatMessage("twitch", f"p{i}", "chan", f"author{i}", "SPECIMEN_AUTHOR_NAME", text, 1_700_000_000.0)


async def test_no_flagged_text_private_term_or_phone_reaches_logs(caplog: pytest.LogCaptureFixture) -> None:
    rng = random.Random(7)
    secret_term, secret_phone = "zzqq secret name", "9876501234"
    config = ChatConfig(
        classify=ClassifyConfig(llm_flush_ms=2, llm_pending_max_s=0.3, llm_timeout_ms=80),
        streamer_private=StreamerPrivateConfig(terms=[secret_term], phones=["+91 " + secret_phone]),
    )
    flaky = FakeLlm(
        [
            "{broken",  # parse failure
            RuntimeError("boom zzuniq-exception"),  # unexpected transport error (not an LlmError)
        ],
        default=lambda texts: reply([ok(i, ["abuse"], 0.9) for i in range(len(texts))]),
    )
    clf = ChatClassifier(config, llm=flaky)
    templates = [
        "madarchod ZZUNIQ{i}",
        "bc ZZUNIQ{i} what a play",
        "call 98765 01234 ZZUNIQ{i}",
        "zzqq secret name ZZUNIQ{i}",
        "upi://pay?pa=zzuniq{i}@ybl",
        "free diamonds bit.ly/zzuniq{i}",
        "saale ZZUNIQ{i}",
        "chutiya harami ZZUNIQ{i}",
    ]
    texts = [rng.choice(templates).format(i=i) for i in range(200)]
    with caplog.at_level(logging.DEBUG):
        verdicts = await clf.classify_final([_msg(t, i) for i, t in enumerate(texts)])
        await clf.aclose()
    assert sum(v.action is not ChatAction.ALLOW for v in verdicts) >= 190
    blob = "\n".join(
        [r.getMessage() for r in caplog.records]
        + [str(r.exc_text or "") + str(r.args) for r in caplog.records]
        + [repr(v) for v in verdicts]
        + [repr(clf), repr(config.streamer_private), repr(_msg("madarchod ZZUNIQ", 0))]
    ).lower()
    for needle in (
        "zzuniq",
        "zzqq",
        "9876501234",
        "98765 01234",
        "madarchod",
        "chutiya",
        "specimen_author_name",
        "bit.ly",
    ):
        assert needle not in blob, needle


async def test_classifier_errors_do_not_carry_text(caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(FakeLlm):
        async def complete(self, **kw) -> str:
            raise ValueError("secret zzuniq text")

    clf = ChatClassifier(
        ChatConfig(classify=ClassifyConfig(llm_pending_max_s=0.2, llm_flush_ms=2)), llm=Exploding()
    )
    with caplog.at_level(logging.DEBUG):
        [v] = await clf.classify_final([_msg("bc ZZUNIQ", 1)])
        await clf.aclose()
    assert v.action is ChatAction.HOLD_FOR_MOD
    assert "zzuniq" not in "\n".join(r.getMessage() for r in caplog.records).lower()


# --- latency ------------------------------------------------------------------------------------------


def _mixed_1000() -> list[ChatMessage]:
    samples = (build_set(11) * 2)[:1000]
    return make_messages(samples)


async def test_rules_and_provisional_path_p95_under_budget() -> None:
    clf = ChatClassifier(bench_config(), llm=FakeLlm())
    msgs = _mixed_1000()
    times = []
    for m in msgs:
        t0 = time.perf_counter()
        clf.classify_fast(m)
        times.append((time.perf_counter() - t0) * 1000.0)
    p95 = percentile(times, 0.95)
    assert p95 <= 20.0, p95  # spec target; the hard cap is 300 ms
    assert percentile(times, 0.99) <= 300.0
    await clf.aclose()


async def test_busy_chat_batches_stay_inside_the_budget() -> None:
    clf = ChatClassifier(bench_config(), llm=FakeLlm())
    msgs = _mixed_1000()
    batch_ms = []
    for i in range(0, len(msgs), 50):
        t0 = time.perf_counter()
        await clf.classify(msgs[i : i + 50])
        batch_ms.append((time.perf_counter() - t0) * 1000.0)
    assert percentile(batch_ms, 0.95) <= 300.0
    await clf.aclose()


async def test_llm_path_p95_with_fake_client() -> None:
    config = ChatConfig(classify=ClassifyConfig(llm_flush_ms=5, llm_timeout_ms=250))
    clf = ChatClassifier(config, llm=FakeLlm(delay_s=0.02))
    borderline = [
        _msg(t, i)
        for i, t in enumerate(
            ["bc what a play", "saale ne headshot maar diya", "bit.ly/specimen", "idiot mode on lol"] * 10
        )
    ]

    async def one(m: ChatMessage):
        t0 = time.perf_counter()
        [v] = await clf.classify_final([m])
        return v, (time.perf_counter() - t0) * 1000.0

    results = await asyncio.gather(*(one(m) for m in borderline))
    assert all(v.stage == "llm" for v, _ in results)
    assert percentile([ms for _, ms in results], 0.95) <= 250.0
    await clf.aclose()


# --- eval-set acceptance (docs/specs/chat.md, Gate 4 thresholds) -----------------------------------------


async def test_synthetic_set_meets_gate4_thresholds_rules_only() -> None:
    samples = build_set(4242)
    clf = ChatClassifier(bench_config())  # no LLM: local fallback
    verdicts = [clf.classify_fast(m).verdict for m in make_messages(samples)]
    r = score(samples, verdicts)
    assert r["abuse"]["recall"] >= 0.85 and r["abuse"]["precision"] >= 0.90, r["abuse"]
    assert r["scam"]["recall"] >= 0.95, r["scam"]
    assert r["fake_upi"]["recall"] >= 0.98, r["fake_upi"]
    assert r["doxxing"]["recall"] >= 0.85, r["doxxing"]
    assert r["clean_false_positive"] <= 0.03, r["clean_false_positive"]


def test_no_message_text_in_the_benchmark_report() -> None:
    samples = build_set(4242)[:50]
    clf = ChatClassifier(bench_config())
    verdicts = [clf.classify_fast(m).verdict for m in make_messages(samples)]
    report = repr(score(samples, verdicts))
    assert not any(re.escape(s.text) in report for s in samples if len(s.text) > 8)
