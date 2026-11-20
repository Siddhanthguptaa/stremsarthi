"""STT race (docs/specs/audio.md 5.2): primary first, hedge after ``race_hedge_s``, failover on error.

First valid result wins (a successful call with empty text is valid), the others are cancelled. Every call is
async with a timeout and a token bucket per provider. A provider that errors or answers 429 is put in a
cool-down (30 s) so the next segments go straight to the failover. Groq needs >= 3 s of audio per request, so
it receives the segment plus the audio before it (``context``) and only the words of the segment are kept.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from audio.segmenter import Segment
from audio.stt.base import (
    SttBusy,
    SttRateLimited,
    SttUnavailable,
    TokenBucket,
    with_seg_id,
)
from contracts.protocols import SpeechToText
from contracts.speech import Transcript

log = logging.getLogger("audio.orchestrator")

DEFAULT_TIMEOUTS = {"sarvam": 1.8, "groq": 1.5, "local": 1.5}
Context = Callable[[Segment, float], tuple[Any, float]]


class SttAllFailed(RuntimeError):
    """No provider produced a valid result for the segment."""


@dataclass(slots=True)
class _State:
    stt: SpeechToText
    timeout_s: float
    bucket: TokenBucket | None
    cooldown_until: float = 0.0
    failures: int = 0
    disabled: bool = False


class SttRace:
    def __init__(
        self,
        providers: Mapping[str, SpeechToText],
        order: Sequence[str] = ("sarvam", "local"),
        *,
        hedge_s: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
        rpm: Mapping[str, float | None] | None = None,
        timeouts: Mapping[str, float] | None = None,
        failover: Sequence[str] = ("groq",),
        cooldown_s: float = 30.0,
        groq_min_s: float = 3.0,
    ) -> None:
        rpm = {"sarvam": 55.0, "groq": 18.0, "local": None, **(rpm or {})}
        tmo = {**DEFAULT_TIMEOUTS, **(timeouts or {})}
        self._clock = clock
        self._st: dict[str, _State] = {}
        for name, stt in providers.items():
            r = rpm.get(name)
            self._st[name] = _State(
                stt, tmo.get(name, 1.5), TokenBucket(r, 5 if name == "sarvam" else 2, clock) if r else None
            )
        self.order = [n for n in order if n in self._st]
        self.failover = [n for n in failover if n in self._st and n not in self.order]
        self.hedge_s = hedge_s
        self.cooldown_s = cooldown_s
        self.groq_min_s = groq_min_s
        self.wins: dict[str, int] = dict.fromkeys(self._st, 0)
        self.errors: dict[str, int] = dict.fromkeys(self._st, 0)
        self.hedges = 0

    @property
    def providers(self) -> dict[str, SpeechToText]:
        return {n: s.stt for n, s in self._st.items()}

    # ------------------------------------------------------------------ health
    def _healthy(self, name: str) -> bool:
        s = self._st[name]
        if s.disabled or s.cooldown_until > self._clock():
            return False
        return s.bucket is None or s.bucket.retry_after_s() <= 0.0

    def _plan(self) -> tuple[str | None, list[str], list[str]]:
        """(first provider, hedge candidates, failover candidates), healthy ones only.

        The hedge (slow answer) goes to the local engine; an error goes to the failover (Groq) first, then local.
        With the primary in cool-down the failover leads and the local engine is its hedge."""
        order = [n for n in self.order if self._healthy(n)]
        fo = [n for n in self.failover if self._healthy(n)]
        if self.order and self.order[0] in order:
            return order[0], order[1:], fo + order[1:]
        lead = fo + order
        return (lead[0] if lead else None), lead[1:], lead[1:]

    def _note_failure(self, name: str, exc: BaseException) -> None:
        s = self._st[name]
        self.errors[name] += 1
        if isinstance(exc, SttBusy):
            return
        if isinstance(exc, SttUnavailable):
            s.disabled = True
            return
        if name == "local":
            return  # the last resort is never cooled down
        s.failures += 1
        if isinstance(exc, SttRateLimited):
            wait = getattr(exc, "retry_after_s", None) or 0.0
            s.cooldown_until = self._clock() + max(self.cooldown_s, wait)
        elif not isinstance(exc, TimeoutError) or s.failures >= 2:
            s.cooldown_until = self._clock() + self.cooldown_s

    # ------------------------------------------------------------------ calls
    async def _call(self, name: str, seg: Segment, lang: str | None, context: Context | None) -> Transcript:
        s = self._st[name]
        if s.bucket is not None and not s.bucket.try_acquire():
            raise SttRateLimited("local token bucket empty", s.bucket.retry_after_s())
        pcm, start = seg.pcm, seg.pts_start
        wide = name == "groq" and context is not None and seg.duration < self.groq_min_s
        if wide:
            assert context is not None
            pcm, start = context(seg, self.groq_min_s)
        tr = await asyncio.wait_for(s.stt.transcribe(pcm, start, lang), timeout=s.timeout_s + 0.05)
        if wide:
            tr = _restrict(tr, seg)
        return tr

    async def transcribe(
        self, seg: Segment, lang: str | None = None, context: Context | None = None
    ) -> Transcript:
        first, hedges, failovers = self._plan()
        if first is None:
            raise SttAllFailed("no speech-to-text provider available")
        tasks: dict[asyncio.Task[Transcript], str] = {}
        started: set[str] = set()

        def start(candidates: Sequence[str]) -> bool:
            for name in candidates:
                if name not in started:
                    started.add(name)
                    tasks[asyncio.ensure_future(self._call(name, seg, lang, context))] = name
                    return True
            return False

        start([first])
        hedge_at = self._clock() + self.hedge_s
        hedged = False
        try:
            while True:
                timeout = None if hedged else max(0.0, hedge_at - self._clock())
                done, _ = await asyncio.wait(set(tasks), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:  # hedge timer: the primary is slow
                    hedged = True
                    if start(hedges):
                        self.hedges += 1
                    continue
                failed = False
                for t in done:
                    name = tasks.pop(t)
                    exc = t.exception()
                    if exc is None:
                        self._st[name].failures = 0
                        self.wins[name] += 1
                        return with_seg_id(t.result(), seg.seg_id)
                    self._note_failure(name, exc)
                    failed = True
                    log.info("stt %s failed (%s)", name, type(exc).__name__)
                if failed:
                    hedged = True  # a failover replaces the timed hedge
                    if not start(failovers) and not tasks:
                        raise SttAllFailed("every speech-to-text provider failed")
        finally:
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


def _restrict(tr: Transcript, seg: Segment) -> Transcript:
    """Keep the words of a wide (context) transcript that belong to ``seg``."""
    words = tuple(w for w in tr.words if (w.t0 + w.t1) / 2 >= seg.pts_start)
    text = " ".join(w.text for w in words)
    return Transcript(seg.pts_start, seg.pts_end, text, words, tr.lang, tr.provider, tr.latency_s)
