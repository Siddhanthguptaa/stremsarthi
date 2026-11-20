"""Groq LLM stage for the unclear cases (docs/specs/chat.md 5.1 step 7).

``LlmStage`` batches message texts (up to ``llm_batch_max``, flushed after ``llm_flush_ms``), keeps within
30 requests/min and the token-per-minute governor, calls the transport with a per-call timeout and
retries a failed message once. A message that cannot be served within ``llm_pending_max_s`` resolves to
``None``, and the cascade then falls back to the local rules decision. The stage never blocks the
caller's fast path: ``classify`` is awaited from a background task, never from the verdict path.

Only message texts go out (by index, truncated to 500 chars): never author names or ids. Texts are
never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from chat.common import Clock, Secret, SystemClock, TokenBucket, log_event
from contracts.config import ClassifyConfig

_LOG = logging.getLogger("chat.classify.llm")

SYSTEM_PROMPT = (
    "Classify each chat message for a live stream of an Indian streamer. Messages may be Hinglish "
    "(Hindi in Latin letters), Devanagari, Kannada, Tamil, Telugu, Bengali or English. Return JSON only. "
    'Schema: {"results":[{"i":int,"labels":["abuse"|"doxxing"|"scam"|"fake_upi"|"clean"],'
    '"confidence":0..1,"target":"streamer"|"viewer"|"none"}]}. '
    'One result per message index. Use "clean" when the message is harmless, including friendly banter.'
)
KNOWN_LABELS = frozenset({"abuse", "doxxing", "scam", "fake_upi"})
MAX_CHARS = 500
QUEUE_CAP = 1000
_RETRY_DELAY_S = 0.05


class LlmError(Exception):
    """Transport failure (network, 5xx, bad response)."""


class LlmRateLimited(LlmError):
    def __init__(self, retry_after_s: float = 2.0) -> None:
        super().__init__("rate_limited")
        self.retry_after_s = retry_after_s


class LlmTransport(Protocol):
    async def complete(self, *, system: str, user: str, model: str, timeout_s: float, max_tokens: int) -> str:
        """Return the raw JSON text of the model reply. Raise ``LlmRateLimited`` on 429, ``LlmError`` otherwise."""
        ...


@dataclass(frozen=True, slots=True)
class LlmResult:
    labels: frozenset[str]  # subset of KNOWN_LABELS; empty means clean
    confidence: float
    target: str  # streamer | viewer | none

    @property
    def clean(self) -> bool:
        return not self.labels


def estimate_tokens(text: str) -> int:
    """Spec 5.1.7: ``len(chars)/3 + 120`` per message."""
    return int(min(len(text), MAX_CHARS) / 3) + 120


def build_user_prompt(texts: list[str]) -> str:
    payload = {"messages": [{"i": i, "t": t[:MAX_CHARS]} for i, t in enumerate(texts)]}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_response(raw: str, n: int) -> dict[int, LlmResult]:
    """Valid per-index results. Raises ``ValueError`` when the reply is not the expected JSON at all."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("llm_bad_json") from exc
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("llm_bad_schema")
    out: dict[int, LlmResult] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        i, labels, conf = r.get("i"), r.get("labels"), r.get("confidence")
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < n or i in out:
            continue
        if not isinstance(labels, list) or isinstance(conf, bool) or not isinstance(conf, int | float):
            continue
        if not 0.0 <= float(conf) <= 1.0:
            continue
        names = {x for x in labels if isinstance(x, str)}
        unknown = names - KNOWN_LABELS - {"clean"}
        if unknown:
            log_event(_LOG, "llm_unknown_label", level=logging.WARNING, count=len(unknown))
        target = r.get("target")
        out[i] = LlmResult(
            frozenset(names & KNOWN_LABELS),
            float(conf),
            target if target in ("streamer", "viewer", "none") else "none",
        )
    return out


@dataclass(slots=True)
class _Item:
    text: str
    fut: asyncio.Future[LlmResult | None]
    ready_at: float
    deadline: float
    tokens: int
    attempts: int = 0
    exhausted: bool = False


@dataclass(slots=True)
class LlmStats:
    calls: int = 0
    failures: int = 0
    rate_limited: int = 0
    timeouts: int = 0
    expired: int = 0
    sent: int = 0
    latencies_ms: list[float] = field(default_factory=list)


class LlmStage:
    def __init__(
        self,
        transport: LlmTransport,
        config: ClassifyConfig | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._transport = transport
        self._cfg = config or ClassifyConfig()
        self._clock: Clock = clock or SystemClock()
        mono = self._clock.monotonic
        self._rpm = TokenBucket(
            self._cfg.llm_rpm / 60.0, float(self._cfg.llm_rpm), clock=mono, sleep=self._clock.sleep
        )
        self._tpm = TokenBucket(
            self._cfg.llm_tpm / 60.0, float(self._cfg.llm_tpm), clock=mono, sleep=self._clock.sleep
        )
        self._queue: deque[_Item] = deque()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._blocked_until = 0.0
        self._inflight: list[_Item] = []
        self.stats = LlmStats()

    # --- public ---------------------------------------------------------------------------------

    async def classify(self, text: str) -> LlmResult | None:
        """The LLM verdict for one message, or ``None`` when unavailable within ``llm_pending_max_s``."""
        if len(self._queue) >= QUEUE_CAP:
            return None
        loop = asyncio.get_running_loop()
        now = self._clock.monotonic()
        item = _Item(
            text=text[:MAX_CHARS],
            fut=loop.create_future(),
            ready_at=now,
            deadline=now + self._cfg.llm_pending_max_s,
            tokens=estimate_tokens(text),
        )
        self._queue.append(item)
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run())
        self._wake.set()
        return await item.fut

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for item in [*self._inflight, *self._queue]:
            if not item.fut.done():
                item.fut.set_result(None)
        self._inflight, self._queue = [], deque()

    # --- batching loop --------------------------------------------------------------------------

    def _expire(self, now: float) -> None:
        keep: deque[_Item] = deque()
        for item in self._queue:
            if item.fut.done():
                continue
            if item.deadline <= now:
                self.stats.expired += 1
                item.fut.set_result(None)
            else:
                keep.append(item)
        self._queue = keep

    def _take_batch(self) -> list[_Item]:
        batch: list[_Item] = []
        tokens = 0
        for item in self._queue:
            if item.exhausted:
                continue
            if len(batch) >= self._cfg.llm_batch_max or (batch and tokens + item.tokens > self._tpm.capacity):
                break
            batch.append(item)
            tokens += item.tokens
        return batch

    async def _run(self) -> None:
        try:
            await self._loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leave callers hanging: they fall back to the rules decision
            log_event(_LOG, "llm_loop_crashed", level=logging.ERROR, error=type(exc).__name__)
            for item in [*self._inflight, *self._queue]:
                if not item.fut.done():
                    item.fut.set_result(None)
            self._inflight, self._queue = [], deque()

    async def _loop(self) -> None:
        flush_s = self._cfg.llm_flush_ms / 1000.0
        while True:
            now = self._clock.monotonic()
            self._expire(now)
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            live = [i for i in self._queue if not i.exhausted]
            if not live:  # only items waiting out their deadline after a failed retry
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), min(0.05, max(0.001, min(i.deadline for i in self._queue) - now))
                    )
                except TimeoutError:
                    pass
                continue
            if len(live) < self._cfg.llm_batch_max:
                due = min(i.ready_at for i in live) + flush_s
                if now < due:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), due - now)
                    except TimeoutError:
                        pass
                    else:
                        continue  # new message: re-evaluate (maybe full now)
            batch = self._take_batch()
            tokens = sum(i.tokens for i in batch)
            wait = max(
                self._rpm.wait_time(1),
                self._tpm.wait_time(min(tokens, self._tpm.capacity)),
                self._blocked_until - now,
            )
            if wait > 0:
                nearest = min(i.deadline for i in self._queue)
                await self._clock.sleep(max(0.001, min(wait, nearest - now, 1.0)))
                continue
            self._rpm.try_acquire(1)
            self._tpm.try_acquire(min(tokens, self._tpm.capacity))
            for item in batch:
                self._queue.remove(item)
            self._inflight = batch
            await self._call(batch)
            self._inflight = []

    async def _call(self, batch: list[_Item]) -> None:
        self.stats.calls += 1
        self.stats.sent += len(batch)
        started = self._clock.monotonic()
        results: dict[int, LlmResult] = {}
        try:
            raw = await asyncio.wait_for(
                self._transport.complete(
                    system=SYSTEM_PROMPT,
                    user=build_user_prompt([i.text for i in batch]),
                    model=self._cfg.llm_model,
                    timeout_s=self._cfg.llm_timeout_ms / 1000.0,
                    max_tokens=60 * len(batch) + 400,
                ),
                self._cfg.llm_timeout_ms / 1000.0,
            )
            results = parse_response(raw, len(batch))
        except LlmRateLimited as exc:
            self.stats.rate_limited += 1
            self._blocked_until = self._clock.monotonic() + max(0.05, exc.retry_after_s)
            log_event(_LOG, "llm_rate_limited", level=logging.WARNING)
        except TimeoutError:
            self.stats.timeouts += 1
            log_event(_LOG, "llm_timeout", level=logging.WARNING)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # LlmError, bad JSON (ValueError), network errors, SDK surprises
            log_event(_LOG, "llm_failed", level=logging.WARNING, error=type(exc).__name__)
        self.stats.latencies_ms.append((self._clock.monotonic() - started) * 1000.0)
        if len(self.stats.latencies_ms) > 2000:
            del self.stats.latencies_ms[:1000]
        now = self._clock.monotonic()
        for idx, item in enumerate(batch):
            res = results.get(idx)
            if res is not None:
                if not item.fut.done():
                    item.fut.set_result(res)
                continue
            self.stats.failures += 1
            item.attempts += 1
            if item.attempts >= 2:
                item.exhausted = True  # stays queued until its deadline, then resolves to None
            else:
                item.ready_at = now + _RETRY_DELAY_S
            if not item.fut.done():
                self._queue.appendleft(item)
        self._wake.set()


class GroqTransport:
    """``LlmTransport`` over the Groq SDK (JSON mode, temperature 0, low reasoning effort).

    The key is a ``Secret``; the SDK client is created lazily so importing this module costs nothing.
    Live use is optional: with no key, build the cascade without an LLM and it uses the rules fallback.
    """

    def __init__(self, api_key: Secret, *, client: Any | None = None) -> None:
        self._key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import groq

            self._client = groq.AsyncGroq(api_key=self._key.reveal(), max_retries=0)
        return self._client

    async def complete(self, *, system: str, user: str, model: str, timeout_s: float, max_tokens: int) -> str:
        import groq

        try:
            resp = await self._get_client().chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                response_format={"type": "json_object"},
                reasoning_effort="low",
                max_completion_tokens=max_tokens,
                timeout=timeout_s,
            )
        except groq.RateLimitError as exc:
            retry = 2.0
            headers = getattr(getattr(exc, "response", None), "headers", None)
            if headers is not None:
                try:
                    retry = float(headers.get("retry-after", retry))
                except (TypeError, ValueError):
                    pass
            raise LlmRateLimited(retry) from None
        except groq.APIError as exc:
            raise LlmError(type(exc).__name__) from None
        content = resp.choices[0].message.content if resp.choices else None
        if not content:
            raise LlmError("empty_reply")
        return content


def groq_transport_from_env() -> GroqTransport | None:
    """A transport when ``GROQ_API_KEY`` is in the process environment, else ``None`` (rules-only)."""
    key = os.environ.get("GROQ_API_KEY", "").strip()
    return GroqTransport(Secret(key)) if key else None
