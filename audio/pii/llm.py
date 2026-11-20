"""Groq fallback for ambiguous spoken-PII cases (docs/specs/audio.md 5.7).

Only ambiguous spans are sent (at most 40 tokens, no audio). Prompts, responses and tokens are never logged:
log lines carry counts, latency and category codes only. Every failure (no key, rate limit, timeout, HTTP error,
unparsable reply) raises ``LlmError``/``TimeoutError`` and the caller falls back to the rules (5.7).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from audio.stt.base import TokenBucket
from contracts.enums import Category

log = logging.getLogger("audio.pii")

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_LLM_MODEL = "openai/gpt-oss-20b"
MAX_TOKENS_IN = 40
CATEGORY_MAP: dict[str, Category | None] = {
    "phone": Category.SPOKEN_PHONE,
    "upi": Category.SPOKEN_UPI,
    "address": Category.SPOKEN_ADDRESS,
    "other_pii": Category.SPOKEN_OTHER_PII,
    "none": None,
}

SYSTEM_PROMPT_V1 = (  # pii_llm_v1: fixed, never built from user text
    "You label spoken personal information in a short token list from a speech transcript of an Indian live "
    "streamer (Hindi, English or mixed, numbers may be spelled as words). The user message is JSON "
    '{"tokens": [...], "cue_active": bool}. Find spans of tokens that reveal a phone number, a UPI id, '
    "a postal address, or another private number (OTP, PIN, account, card, ID). Answer with JSON only, "
    'exactly {"spans": [{"start_token": int, "end_token": int, "category": '
    '"phone"|"upi"|"address"|"other_pii"|"none", "confidence": number}]}. Token indices are zero based and '
    'inclusive. Use category "none" or an empty list when nothing private is present. Never explain.'
)


class LlmError(RuntimeError):
    """Any failure of the LLM path. Messages never contain prompts, tokens or replies."""


class LlmUnavailable(LlmError):
    pass


class LlmRateLimited(LlmError):
    pass


class LlmParseError(LlmError):
    pass


@dataclass(frozen=True, slots=True)
class LlmSpan:
    start_token: int
    end_token: int
    category: Category | None  # None means "none"
    confidence: float


class LlmBackend(Protocol):
    async def complete(self, system: str, user: str, timeout_s: float) -> str: ...


class GroqChatBackend:
    """Groq chat completions over httpx. Structured output on ``gpt-oss-20b`` is UNVERIFIED: ``json_object`` mode
    and ``reasoning_effort`` are sent first and dropped for good after a 400, falling back to plain JSON."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = GROQ_LLM_MODEL,
        url: str = GROQ_CHAT_URL,
        max_tokens: int = 200,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")
        self.model, self.url, self.max_tokens = model, url, max_tokens
        self._client = client
        self._owns_client = client is None
        self._extras = True

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _http(self, timeout_s: float) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def complete(self, system: str, user: str, timeout_s: float) -> str:
        if not self._key:
            raise LlmUnavailable("no GROQ_API_KEY")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if self._extras:
            body["response_format"] = {"type": "json_object"}
            body["reasoning_effort"] = "low"
        try:
            resp = await self._http(timeout_s).post(
                self.url,
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError("groq llm timeout") from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"groq llm transport error ({type(exc).__name__})") from exc
        if resp.status_code == 429:
            raise LlmRateLimited("groq llm 429")
        if resp.status_code == 400 and self._extras:
            self._extras = False  # structured output not accepted: plain JSON from now on
            raise LlmError("groq llm 400 (retrying without json mode next time)")
        if resp.status_code >= 400:
            raise LlmError(f"groq llm HTTP {resp.status_code}")
        try:
            return str(resp.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmParseError("groq llm reply has no content") from exc


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_response(text: str, n_tokens: int) -> list[LlmSpan]:
    """Strict parse. Indices are validated against the input length; anything else is ``LlmParseError``."""
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except (ValueError, TypeError) as exc:
        raise LlmParseError("llm reply is not JSON") from exc
    spans = data.get("spans") if isinstance(data, dict) else None
    if not isinstance(spans, list) or len(spans) > 20:
        raise LlmParseError("llm reply has no valid spans list")
    out: list[LlmSpan] = []
    for s in spans:
        if not isinstance(s, dict):
            raise LlmParseError("llm span is not an object")
        a, b, cat, conf = s.get("start_token"), s.get("end_token"), s.get("category"), s.get("confidence")
        if (
            not isinstance(a, int) or isinstance(a, bool) or not isinstance(b, int) or isinstance(b, bool)
            or not 0 <= a <= b < n_tokens
            or not isinstance(cat, str) or cat not in CATEGORY_MAP
            or isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0
        ):  # fmt: skip
            raise LlmParseError("llm span failed validation")
        out.append(LlmSpan(a, b, CATEGORY_MAP[cat], float(conf)))
    return out


class PiiLlm:
    """Rate-limited, time-limited classifier over a ``LlmBackend``."""

    def __init__(
        self,
        backend: LlmBackend,
        *,
        timeout_s: float = 0.7,
        rpm: float = 25.0,
        burst: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.timeout_s = timeout_s
        self.bucket = TokenBucket(rpm, burst, clock)
        self.calls = 0
        self.failures = 0

    async def classify(self, tokens: Sequence[str], cue_active: bool) -> list[LlmSpan]:
        toks = list(tokens)[:MAX_TOKENS_IN]
        if not toks:
            return []
        if not self.bucket.try_acquire():
            self.failures += 1
            raise LlmRateLimited("local llm token bucket empty")
        self.calls += 1
        user = json.dumps({"tokens": toks, "cue_active": bool(cue_active)}, ensure_ascii=False)
        t0 = time.monotonic()
        try:
            text = await asyncio.wait_for(
                self.backend.complete(SYSTEM_PROMPT_V1, user, self.timeout_s), self.timeout_s
            )
            spans = parse_response(text, len(toks))
        except BaseException as exc:
            self.failures += 1
            if isinstance(exc, asyncio.CancelledError):
                raise
            log.info(
                "pii llm failed: %s after %.2fs (n_tokens=%d)",
                type(exc).__name__,
                time.monotonic() - t0,
                len(toks),
            )
            raise
        log.info(
            "pii llm ok: %.2fs n_tokens=%d categories=%s",
            time.monotonic() - t0, len(toks), sorted({s.category.value if s.category else "none" for s in spans}),
        )  # fmt: skip
        return spans
