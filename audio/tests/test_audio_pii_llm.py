"""T3.3: Groq fallback for ambiguous spoken PII (docs/specs/audio.md 5.7), mock-tested. Live check skipped without a key."""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import pytest

from audio.pii.detector import SpokenPiiDetector
from audio.pii.llm import (
    SYSTEM_PROMPT_V1,
    GroqChatBackend,
    LlmParseError,
    PiiLlm,
    parse_response,
)
from audio.pii.rules import SpokenRules
from contracts.enums import Category
from contracts.speech import TranscriptWindow, Word

AMBIGUOUS = "so the code is one two three four five six"  # 6 digits, no cue: ambiguous (0.5)
CANARY = "zzcanaryzz"


def window(text: str) -> TranscriptWindow:
    ws = [Word(t, i * 0.35, i * 0.35 + 0.28, False, 1) for i, t in enumerate(text.split())]
    return TranscriptWindow(tuple(ws), 0.0, ws[-1].t1, 0)


class Stub:
    def __init__(self, reply: str | BaseException = "", delay: float = 0.0) -> None:
        self.reply, self.delay = reply, delay
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, timeout_s: float) -> str:
        self.calls.append((system, user))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def detect(stub: Stub, text: str = AMBIGUOUS, **kw):
    llm = PiiLlm(stub, timeout_s=kw.pop("timeout_s", 0.7))
    det = SpokenPiiDetector(SpokenRules(), llm)
    return asyncio.run(det.detect(window(text))), det


def reply(cat: str, a: int = 5, b: int = 9, conf: float = 0.95) -> str:
    return json.dumps({"spans": [{"start_token": a, "end_token": b, "category": cat, "confidence": conf}]})


def test_valid_confirmation_sets_the_llm_category():
    spans, det = detect(Stub(reply("other_pii", 4, 9)))
    assert len(spans) == 1 and spans[0].category is Category.SPOKEN_OTHER_PII and not spans[0].ambiguous
    assert spans[0].reason == "llm_confirm" and det.stats["llm_confirmed"] == 1


def test_valid_none_drops_the_span():
    spans, det = detect(Stub(json.dumps({"spans": []})))
    assert spans == [] and det.stats["llm_dropped"] == 1
    spans, _ = detect(Stub(reply("none")))
    assert spans == []


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"spans": "x"}),
        json.dumps({"spans": [{"start_token": 0, "end_token": 99, "category": "phone", "confidence": 0.9}]}),
        json.dumps({"spans": [{"start_token": 3, "end_token": 1, "category": "phone", "confidence": 0.9}]}),
        json.dumps({"spans": [{"start_token": 0, "end_token": 1, "category": "ssn", "confidence": 0.9}]}),
        json.dumps({"spans": [{"start_token": 0, "end_token": 1, "category": "phone", "confidence": 7}]}),
        json.dumps({"spans": [{"start_token": True, "end_token": 1, "category": "phone", "confidence": 1}]}),
    ],
)
def test_invalid_replies_are_failures_and_follow_the_fallback(bad):
    with pytest.raises(LlmParseError):
        parse_response(bad, 10)
    spans, det = detect(Stub(bad))
    assert det.stats["llm_failed"] == 1
    assert len(spans) == 1 and spans[0].ambiguous and spans[0].reason == "llm_fallback_mute"  # 6 digits: mute


def test_slow_reply_times_out_at_0_7s_and_falls_back():
    spans, det = detect(Stub(reply("none"), delay=2.0), timeout_s=0.1)
    assert det.stats["llm_failed"] == 1 and len(spans) == 1 and spans[0].reason == "llm_fallback_mute"


def test_429_falls_back_and_bucket_limits_calls():
    from audio.pii.llm import LlmRateLimited

    spans, det = detect(Stub(LlmRateLimited("429")))
    assert len(spans) == 1 and spans[0].ambiguous
    llm = PiiLlm(Stub('{"spans": []}'), rpm=25, burst=3)
    for _ in range(3):
        asyncio.run(llm.classify(["a"], False))
    with pytest.raises(LlmRateLimited):
        asyncio.run(llm.classify(["a"], False))


def test_fallback_outside_watch_window_and_under_six_digits_passes():
    # ambiguous but not 6+ digits and no cue: an unknown-handle UPI id with a failing LLM passes
    spans, _ = detect(Stub(TimeoutError()), "rahul at the rate of foobar")
    assert spans == []
    spans, _ = detect(Stub(TimeoutError()), "mera upi id hai rahul at the rate of foobar")
    assert len(spans) == 1  # watch window: mute


def test_no_llm_configured_uses_the_fallback_without_network():
    spans = asyncio.run(SpokenPiiDetector(SpokenRules(), None).detect(window(AMBIGUOUS)))
    assert len(spans) == 1 and spans[0].reason == "llm_fallback_mute"


def test_request_shape_and_token_cap():
    stub = Stub(reply("none"))
    detect(stub, "aur phir " * 30 + "one two three four five six")
    system, user = stub.calls[0]
    assert system == SYSTEM_PROMPT_V1
    payload = json.loads(user)
    assert set(payload) == {"tokens", "cue_active"} and len(payload["tokens"]) <= 40
    assert payload["cue_active"] is False


def test_logs_never_contain_tokens_or_replies(caplog):
    caplog.set_level(logging.DEBUG)
    text = f"{CANARY} one two three four five six {CANARY}"
    detect(Stub(reply("other_pii", 0, 3)), text)
    detect(Stub(f"garbage {CANARY}"), text)
    detect(Stub(TimeoutError(CANARY)), text)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert CANARY not in blob and "one two three" not in blob
    assert "pii llm" in blob  # counts and latency are logged


def test_groq_backend_http_shape_and_json_mode_downgrade():
    seen: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        assert req.headers["authorization"] == "Bearer k"
        if "response_format" in body and len(seen) == 1:
            return httpx.Response(400, json={"error": "x"})
        return httpx.Response(200, json={"choices": [{"message": {"content": reply("phone", 0, 1)}}]})

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        be = GroqChatBackend("k", client=client)
        from audio.pii.llm import LlmError

        with pytest.raises(LlmError):
            await be.complete("s", "{}", 0.7)
        return await be.complete("s", "{}", 0.7)

    out = asyncio.run(go())
    assert (
        seen[0]["model"] == "openai/gpt-oss-20b"
        and seen[0]["temperature"] == 0
        and seen[0]["max_tokens"] == 200
    )
    assert "response_format" not in seen[1] and json.loads(out)["spans"][0]["category"] == "phone"


def test_groq_backend_maps_429_and_missing_key():
    from audio.pii.llm import LlmRateLimited, LlmUnavailable

    async def go(status):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json={})))
        return await GroqChatBackend("k", client=client).complete("s", "{}", 0.7)

    with pytest.raises(LlmRateLimited):
        asyncio.run(go(429))
    with pytest.raises(LlmUnavailable):
        asyncio.run(GroqChatBackend("").complete("s", "{}", 0.7))


@pytest.mark.skipif(
    not os.environ.get("GROQ_API_KEY"), reason="GROQ_API_KEY not set: live Groq check skipped"
)
def test_live_groq_answers_with_valid_json():
    async def go():
        llm = PiiLlm(GroqChatBackend(), timeout_s=3.0)
        return await llm.classify("my otp is one two three four five six".split(), True)

    assert isinstance(asyncio.run(go()), list)
