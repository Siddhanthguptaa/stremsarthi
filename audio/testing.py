"""Test helpers for the audio module: a virtual-time event loop, a scripted STT and synthetic speech timelines.

Nothing here touches the network or real audio. Speech is a tone while a word is "spoken" (the energy VAD
treats it as speech); the words, their texts and times are the ground truth that ``ScriptedSTT`` returns, as a
perfect recogniser would, so tests can measure mute coverage against known timestamps. All text is synthetic.
"""

from __future__ import annotations

import asyncio
import random
import selectors
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, TypeVar

import numpy as np

from audio.orchestrator.pipeline import AudioPipeline
from contracts.media import AudioChunk
from contracts.speech import Transcript, Word

SR = 16_000
CHUNK_S = 0.02
T = TypeVar("T")


# --------------------------------------------------------------------------- virtual time
class _VirtualSelector(selectors.BaseSelector):
    """Wraps the real selector: instead of sleeping for ``timeout`` it advances the loop's virtual clock."""

    def __init__(self, real: selectors.BaseSelector, loop: VirtualClockLoop) -> None:
        self._real, self._loop = real, loop

    def register(self, fileobj: Any, events: Any, data: Any = None) -> Any:
        return self._real.register(fileobj, events, data)

    def unregister(self, fileobj: Any) -> Any:
        return self._real.unregister(fileobj)

    def modify(self, fileobj: Any, events: Any, data: Any = None) -> Any:
        return self._real.modify(fileobj, events, data)

    def select(self, timeout: float | None = None) -> Any:
        if timeout is None:
            raise RuntimeError("virtual loop deadlock: nothing scheduled and nothing ready")
        if timeout > 0:
            self._loop.advance(timeout)
        return self._real.select(0)

    def close(self) -> None:
        self._real.close()

    def get_map(self) -> Any:
        return self._real.get_map()


class VirtualClockLoop(asyncio.SelectorEventLoop):
    """Event loop whose ``time()`` only moves when every task is waiting on a timer (no real sleeping)."""

    def __init__(self, start: float = 1000.0) -> None:
        super().__init__(selectors.DefaultSelector())
        self._vt = start
        self._selector = _VirtualSelector(self._selector, self)  # type: ignore[has-type]

    def time(self) -> float:
        return self._vt

    def advance(self, dt: float) -> None:
        self._vt += dt


def run_virtual(main: Callable[[VirtualClockLoop], Awaitable[T]], start: float = 1000.0) -> T:
    """Run ``main(loop)`` on a fresh virtual-time loop and return its result."""
    loop = VirtualClockLoop(start)
    try:
        return loop.run_until_complete(main(loop))
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


# --------------------------------------------------------------------------- scripted speech
@dataclass(slots=True)
class Truth:
    text: str
    t0: float
    t1: float
    pii: bool = False
    digit: bool = False


@dataclass(slots=True)
class Timeline:
    """Synthetic utterances: a list of words with times, rendered as tone bursts."""

    words: list[Truth] = field(default_factory=list)
    end: float = 0.0
    step_s: float = 0.32
    word_s: float = 0.27

    def say(
        self, text: str, *, pii: bool = False, digits: bool = False, pause_after: float = 0.0
    ) -> Timeline:
        t = self.end
        for tok in text.split():
            self.words.append(Truth(tok, t, t + self.word_s, pii, digits))
            t += self.step_s
        self.end = t - (self.step_s - self.word_s) + pause_after
        return self

    def pause(self, seconds: float) -> Timeline:
        self.end += seconds
        return self

    def render(self, tail_s: float = 1.0, amp: float = 0.3) -> np.ndarray:
        n = int((self.end + tail_s) * SR)
        x = np.zeros(n, dtype=np.float64)
        # voiced from the first to the last word of a sentence: words closer than 0.2 s stay one burst
        last = None
        for w in self.words:
            a, b = int(w.t0 * SR), int(w.t1 * SR)
            if last is not None and w.t0 - last < 0.18:
                a = int(last * SR)
            t = np.arange(b - a) / SR
            x[a:b] = np.sin(2 * np.pi * (180 + 40 * (sum(map(ord, w.text)) % 5)) * t) * amp
            last = w.t1
        return (x * 32767).astype(np.int16)


class ScriptedSTT:
    """``SpeechToText`` that answers from the ground-truth timeline, like a perfect recogniser with word times."""

    name: ClassVar[str] = "scripted"

    def __init__(
        self,
        timeline: Timeline,
        provider: Literal["sarvam", "groq", "local"] = "sarvam",
        latency: float | Callable[[], float] = 0.4,
        fail: BaseException | Callable[[], BaseException | None] | None = None,
        word_times: bool = True,
        dropout: float = 0.0,
        seed: int = 7,
    ) -> None:
        self.timeline = timeline
        self.provider = provider
        self.latency = latency
        self.fail = fail
        self.word_times = word_times
        self.dropout = dropout
        self.rng = random.Random(seed)
        self.calls = 0
        self.call_times: list[float] = []

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Transcript:
        loop = asyncio.get_running_loop()
        self.calls += 1
        self.call_times.append(loop.time())
        lat = self.latency() if callable(self.latency) else self.latency
        if lat:
            await asyncio.sleep(lat)
        err = self.fail() if callable(self.fail) else self.fail
        if err is not None:
            raise err
        dur = len(pcm16k) / SR
        end = pts_start + dur
        got = [w for w in self.timeline.words if pts_start <= (w.t0 + w.t1) / 2 < end]
        if self.dropout:
            got = [w for w in got if self.rng.random() >= self.dropout]
        if self.word_times:
            words = tuple(Word(w.text, w.t0, w.t1, False, 0) for w in got)
        else:
            step = dur / max(1, len(got))
            words = tuple(
                Word(w.text, pts_start + i * step, pts_start + (i + 1) * step, True, 0)
                for i, w in enumerate(got)
            )
        return Transcript(pts_start, end, " ".join(w.text for w in got), words, "hi", self.provider, lat)


# --------------------------------------------------------------------------- feeding
async def feed_realtime(
    pipe: AudioPipeline, pcm: np.ndarray, *, pts0: float = 0.0, chunk_s: float = CHUNK_S, seq0: int = 0
) -> None:
    """Feed ``pcm`` to the pipeline at real-time pace on the running (virtual) loop, then flush."""
    loop = asyncio.get_running_loop()
    n = int(chunk_s * SR)
    for k, i in enumerate(range(0, len(pcm) - n + 1, n)):
        pipe.on_chunk(AudioChunk(seq0 + k, pts0 + i / SR, SR, pcm[i : i + n], loop.time()))
        await asyncio.sleep(chunk_s)
    pipe.flush()


def intervals_cover(spans: Sequence[tuple[float, float]], lo: float, hi: float) -> float:
    """Seconds of ``[lo, hi]`` covered by the union of ``spans``."""
    pieces = sorted((max(a, lo), min(b, hi)) for a, b in spans if b > lo and a < hi)
    total, cur = 0.0, lo
    for a, b in pieces:
        a = max(a, cur)
        if b > a:
            total += b - a
            cur = b
    return total
