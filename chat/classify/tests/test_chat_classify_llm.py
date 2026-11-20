"""Groq stage (cases 7, 8): contract failures, batching, rate buckets, prompt privacy, transport mapping."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from chat.classify.llm import (
    SYSTEM_PROMPT,
    GroqTransport,
    LlmError,
    LlmRateLimited,
    LlmStage,
    estimate_tokens,
    groq_transport_from_env,
    parse_response,
)
from chat.common import ManualClock, Secret
from chat.testing.fake_llm import FakeLlm, ok, reply
from contracts.config import ClassifyConfig


def stage(llm, clock=None, **kw) -> LlmStage:
    base = {"llm_flush_ms": 5, "llm_pending_max_s": 0.5, "llm_timeout_ms": 100}
    base.update(kw)
    return LlmStage(llm, ClassifyConfig(**base), clock=clock)


# --- parsing ------------------------------------------------------------------------------------------


def test_parse_valid_unknown_label_and_junk_rows() -> None:
    raw = reply(
        [
            ok(0, ["abuse"], 0.9, "streamer"),
            ok(1, ["clean"], 0.8),
            {
                "i": 2,
                "labels": ["weird_label"],
                "confidence": 0.7,
                "target": "viewer",
            },  # unknown label -> clean
            {"i": 3, "labels": ["abuse"], "confidence": 7},  # confidence out of range -> dropped
            {"i": 9, "labels": ["abuse"], "confidence": 0.9},  # index out of range -> dropped
            {"i": 0, "labels": ["scam"], "confidence": 0.9},  # duplicate index -> first wins
            {"labels": ["abuse"], "confidence": 0.9},  # missing index -> dropped
            "garbage",
        ]
    )
    got = parse_response(raw, 4)
    assert set(got) == {0, 1, 2}
    assert got[0].labels == {"abuse"} and got[0].target == "streamer"
    assert got[1].clean and got[2].clean


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"results": 5}', '{"x": 1}'])
def test_parse_rejects_non_schema_replies(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_response(raw, 2)


def test_parse_accepts_fenced_json() -> None:
    assert 0 in parse_response("```json\n" + reply([ok(0)]) + "\n```", 1)


# --- case 7: contract failures -> retried once, then None after the pending deadline --------------------


async def test_malformed_json_is_retried_once_and_then_served() -> None:
    llm = FakeLlm(["{not json", reply([ok(0, ["abuse"], 0.9)])])
    s = stage(llm)
    res = await s.classify("bc")
    assert res is not None and res.labels == {"abuse"} and len(llm.calls) == 2
    await s.aclose()


async def test_missing_index_only_retries_that_message() -> None:
    llm = FakeLlm([reply([ok(0, ["abuse"], 0.9)]), lambda texts: reply([ok(0, ["scam"], 0.9)])])
    s = stage(llm, llm_batch_max=2, llm_flush_ms=50)
    a, b = await asyncio.gather(s.classify("first"), s.classify("second"))
    assert a.labels == {"abuse"} and b.labels == {"scam"}
    assert llm.texts_seen == [["first", "second"], ["second"]]
    await s.aclose()


async def test_unknown_label_is_treated_as_clean() -> None:
    llm = FakeLlm([reply([{"i": 0, "labels": ["hateful"], "confidence": 0.9, "target": "none"}])])
    s = stage(llm)
    res = await s.classify("x")
    assert res is not None and res.clean
    await s.aclose()


async def test_timeout_twice_then_gives_up_at_pending_deadline() -> None:
    llm = FakeLlm(delay_s=0.5)  # slower than the 100 ms per-call timeout
    s = stage(llm, llm_pending_max_s=0.4)
    res = await s.classify("bc")
    assert res is None and s.stats.timeouts == 2 and len(llm.calls) == 2  # tried once, retried once
    await s.aclose()


async def test_rate_limited_429_blocks_then_retries_once() -> None:
    llm = FakeLlm([LlmRateLimited(0.05), reply([ok(0, ["doxxing"], 0.9)])])
    s = stage(llm, llm_pending_max_s=2.0)
    res = await s.classify("x")
    assert res is not None and res.labels == {"doxxing"} and s.stats.rate_limited == 1
    await s.aclose()


async def test_transport_error_twice_returns_none() -> None:
    llm = FakeLlm([LlmError("boom"), LlmError("boom")])
    s = stage(llm, llm_pending_max_s=0.3)
    assert await s.classify("x") is None
    assert s.stats.failures == 2
    await s.aclose()


# --- batching ---------------------------------------------------------------------------------------------


async def test_batches_of_20_flush_when_full_and_remainder_after_flush_window() -> None:
    llm = FakeLlm()
    s = stage(llm, llm_flush_ms=40, llm_pending_max_s=3.0)
    results = await asyncio.gather(*(s.classify(f"m{i}") for i in range(25)))
    assert all(r is not None and r.clean for r in results)
    assert [len(t) for t in llm.texts_seen] == [20, 5]
    await s.aclose()


async def test_prompt_carries_only_texts_by_index_truncated() -> None:
    llm = FakeLlm()
    s = stage(llm)
    await s.classify("x" * 900)
    call = llm.calls[0]
    payload = json.loads(call["user"])
    assert payload == {"messages": [{"i": 0, "t": "x" * 500}]}
    assert call["system"] == SYSTEM_PROMPT and call["model"] == "openai/gpt-oss-20b"
    assert call["timeout_s"] == pytest.approx(0.1)
    await s.aclose()


def test_token_estimate_matches_spec() -> None:
    assert estimate_tokens("a" * 300) == 100 + 120
    assert estimate_tokens("a" * 5000) == 500 // 3 + 120


# --- case 8: buckets -------------------------------------------------------------------------------------


async def test_31st_request_in_a_minute_waits_for_the_bucket() -> None:
    clock = ManualClock()
    stamps: list[float] = []

    class Recording(FakeLlm):
        async def complete(self, **kw) -> str:
            stamps.append(clock.monotonic())
            return await super().complete(**kw)

    s = stage(Recording(), clock=clock, llm_batch_max=1, llm_rpm=30, llm_pending_max_s=60.0, llm_tpm=8000)
    results = await asyncio.gather(*(s.classify(f"msg{i}") for i in range(31)))
    assert all(r is not None for r in results)
    assert len(stamps) == 31
    assert stamps[29] - stamps[0] < 1.0  # the first 30 use the full bucket (burst)
    assert stamps[30] - stamps[0] >= 1.9  # the 31st waits ~2 s (0.5 requests/s)
    await s.aclose()


async def test_tpm_governor_splits_an_oversize_batch_and_waits() -> None:
    clock = ManualClock()
    stamps: list[float] = []

    class Recording(FakeLlm):
        async def complete(self, **kw) -> str:
            stamps.append(clock.monotonic())
            return await super().complete(**kw)

    llm = Recording()
    s = stage(llm, clock=clock, llm_tpm=500, llm_batch_max=20, llm_pending_max_s=60.0)
    long_a, long_b = "a" * 450, "b" * 450  # 270 tokens each: together over the 500/min cap
    await asyncio.gather(s.classify(long_a), s.classify(long_b))
    assert [len(t) for t in llm.texts_seen] == [1, 1]
    assert stamps[1] - stamps[0] >= 1.0
    await s.aclose()


async def test_bucket_empty_for_too_long_resolves_none() -> None:
    clock = ManualClock()
    s = stage(FakeLlm(), clock=clock, llm_batch_max=1, llm_rpm=1, llm_pending_max_s=3.0)
    a, b = await asyncio.gather(s.classify("one"), s.classify("two"))
    assert a is not None and b is None  # 1 request/min: the second never gets a slot within 3 s
    await s.aclose()


# --- Groq transport (mocked SDK client) ---------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, result=None, exc=None) -> None:
        self.kwargs: dict = {}
        self._result, self._exc = result, exc

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self._exc:
            raise self._exc
        return self._result


def _client(comp: _FakeCompletions) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=comp))


async def test_groq_transport_request_shape() -> None:
    reply_obj = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"results":[]}'))])
    comp = _FakeCompletions(reply_obj)
    t = GroqTransport(Secret("specimen-key"), client=_client(comp))
    out = await t.complete(system="S", user="U", model="openai/gpt-oss-20b", timeout_s=0.25, max_tokens=123)
    assert out == '{"results":[]}'
    k = comp.kwargs
    assert k["model"] == "openai/gpt-oss-20b" and k["temperature"] == 0 and k["timeout"] == 0.25
    assert k["response_format"] == {"type": "json_object"} and k["reasoning_effort"] == "low"
    assert k["max_completion_tokens"] == 123 and k["messages"][1] == {"role": "user", "content": "U"}


async def test_groq_transport_maps_429_and_api_errors() -> None:
    import groq

    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    r429 = httpx.Response(429, request=req, headers={"retry-after": "7"})
    t = GroqTransport(
        Secret("k"), client=_client(_FakeCompletions(exc=groq.RateLimitError("x", response=r429, body=None)))
    )
    with pytest.raises(LlmRateLimited) as ei:
        await t.complete(system="S", user="U", model="m", timeout_s=1, max_tokens=1)
    assert ei.value.retry_after_s == 7.0
    r500 = httpx.Response(500, request=req)
    t = GroqTransport(
        Secret("k"),
        client=_client(_FakeCompletions(exc=groq.InternalServerError("x", response=r500, body=None))),
    )
    with pytest.raises(LlmError):
        await t.complete(system="S", user="U", model="m", timeout_s=1, max_tokens=1)
    empty = SimpleNamespace(choices=[])
    with pytest.raises(LlmError):
        await GroqTransport(Secret("k"), client=_client(_FakeCompletions(empty))).complete(
            system="S", user="U", model="m", timeout_s=1, max_tokens=1
        )


def test_transport_from_env_is_none_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert groq_transport_from_env() is None
    monkeypatch.setenv("GROQ_API_KEY", "specimen-key")
    assert groq_transport_from_env() is not None


@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="live Groq check: GROQ_API_KEY is not set")
async def test_live_groq_roundtrip() -> None:
    transport = groq_transport_from_env()
    assert transport is not None
    s = LlmStage(transport, ClassifyConfig(llm_timeout_ms=5000, llm_pending_max_s=10.0))
    res = await s.classify("bhai kya gameplay hai")
    assert res is not None
    await s.aclose()
