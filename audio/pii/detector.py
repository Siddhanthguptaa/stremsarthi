"""``PiiSpanDetector`` for spoken PII: rules first, Groq only for ambiguous spans (docs/specs/audio.md 5.6-5.7)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from audio.pii.llm import GroqChatBackend, LlmError, LlmSpan, PiiLlm
from audio.pii.merge import Cand
from audio.pii.numbers import Tok
from audio.pii.rules import SpokenRules
from contracts.speech import PiiSpan, TranscriptWindow

log = logging.getLogger("audio.pii")

CONTEXT_BEFORE = 6
CONTEXT_AFTER = 3
MAX_LLM_TOKENS = 40
MIN_LLM_CONFIDENCE = 0.5
Timing = Callable[[str, float, float], None]


def fallback_mutes(c: Cand) -> bool:
    """5.7: with no usable LLM answer an ambiguous span is PII if it is a number run of 6+ digits or lies
    inside a watch window; otherwise it passes."""
    return c.n_digits >= 6 or c.cue_active


class SpokenPiiDetector:
    def __init__(
        self,
        rules: SpokenRules | None = None,
        llm: PiiLlm | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_timing: Timing | None = None,
    ) -> None:
        self.rules = rules or SpokenRules()
        self.llm = llm
        self._clock = clock
        self._on_timing = on_timing
        self.stats = {"windows": 0, "ambiguous": 0, "llm_confirmed": 0, "llm_dropped": 0, "llm_failed": 0}

    async def detect(self, window: TranscriptWindow) -> list[PiiSpan]:
        t0 = self._clock()
        toks, cands = self.rules.analyse(window)
        t1 = self._clock()
        self.stats["windows"] += 1
        if self._on_timing:
            self._on_timing("pii_rules", t0, t1)
        amb = [c for c in cands if c.ambiguous]
        verdicts: dict[int, list[LlmSpan] | None] = {}
        if amb:
            self.stats["ambiguous"] += len(amb)
            results = await asyncio.gather(*(self._ask(c, toks) for c in amb))
            verdicts = {id(c): r for c, r in zip(amb, results, strict=True)}
            if self._on_timing and self.llm is not None:
                self._on_timing("pii_llm", t1, self._clock())
        out: list[PiiSpan] = []
        for c in cands:
            span = self._decide(c, verdicts[id(c)]) if c.ambiguous else self._span(c, c.reason, False)
            if span is not None:
                out.append(span)
        return out

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _span(
        c: Cand, reason: str, ambiguous: bool, category: Any = None, conf: float | None = None
    ) -> PiiSpan:
        return PiiSpan(
            category or c.category,
            c.t0,
            c.t1,
            c.confidence if conf is None else conf,
            reason,
            "segment" if c.approx else "word",
            ambiguous,
        )

    async def _ask(self, c: Cand, toks: Sequence[Tok]) -> list[LlmSpan] | None:
        if self.llm is None:
            return None
        lo = max(0, c.tok0 - CONTEXT_BEFORE)
        hi = min(len(toks) - 1, c.tok1 + CONTEXT_AFTER)
        if hi - lo + 1 > MAX_LLM_TOKENS:
            hi = lo + MAX_LLM_TOKENS - 1
            if c.tok0 > hi:  # the span itself lies beyond the cap: window around it instead
                lo = c.tok0
                hi = min(len(toks) - 1, lo + MAX_LLM_TOKENS - 1)
        try:
            spans = await self.llm.classify([t.text for t in toks[lo : hi + 1]], c.cue_active)
        except (LlmError, TimeoutError):
            self.stats["llm_failed"] += 1
            return None
        # re-base token indices on the whole token list, then keep what overlaps this candidate
        return [LlmSpan(s.start_token + lo, s.end_token + lo, s.category, s.confidence) for s in spans]

    def _decide(self, c: Cand, llm: list[LlmSpan] | None) -> PiiSpan | None:
        if llm is None:  # no key, rate limit, timeout or garbage: 5.7 fallback
            return self._span(c, "llm_fallback_mute", True) if fallback_mutes(c) else None
        hits = [
            s
            for s in llm
            if s.category is not None
            and s.confidence >= MIN_LLM_CONFIDENCE
            and s.start_token <= c.tok1
            and s.end_token >= c.tok0
        ]
        if not hits:
            self.stats["llm_dropped"] += 1
            return None
        best = max(hits, key=lambda s: s.confidence)
        self.stats["llm_confirmed"] += 1
        return self._span(c, "llm_confirm", False, best.category, min(1.0, max(0.7, best.confidence)))


def build_detector(config: Any, *, on_timing: Timing | None = None) -> SpokenPiiDetector:
    """Entry point used by ``audio.orchestrator`` (``audio.pii.build_detector``)."""
    a = config.audio
    rules = SpokenRules(watch_window_s=a.watch_window_s, address_mute=a.address_mute)
    key = config.groq_api_key.get_secret_value() if getattr(config, "groq_api_key", None) else ""
    llm = PiiLlm(GroqChatBackend(key), timeout_s=a.llm_timeout_s, rpm=float(a.llm_rpm)) if key else None
    return SpokenPiiDetector(rules, llm, on_timing=on_timing)
