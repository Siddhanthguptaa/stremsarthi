"""Speech-to-text double with scripted text, latency and failure injection."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Literal

from ..speech import Transcript, Word


class FakeSTT:
    name: ClassVar[str] = "fake"

    def __init__(
        self,
        texts: list[str] | None = None,
        latency_s: float = 0.0,
        fail_with: BaseException | None = None,
        word_times: bool = True,
        provider: Literal["sarvam", "groq", "local"] = "local",
        segment_s: float = 1.5,
    ) -> None:
        self.texts = list(texts or [])
        self.latency_s = latency_s
        self.fail_with = fail_with
        self.word_times = word_times
        self.provider = provider
        self.segment_s = segment_s
        self.calls = 0

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Transcript:
        self.calls += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.fail_with is not None:
            raise self.fail_with
        text = self.texts.pop(0) if self.texts else ""
        tokens = text.split()
        pts_end = pts_start + self.segment_s
        step = self.segment_s / len(tokens) if tokens else 0.0
        words = tuple(
            Word(t, pts_start + i * step, pts_start + (i + 1) * step, approx=not self.word_times, seg_id=0)
            for i, t in enumerate(tokens)
        )
        return Transcript(
            pts_start=pts_start,
            pts_end=pts_end,
            text=text,
            words=words,
            lang=lang_hint or "hi",
            provider=self.provider,
            latency_s=self.latency_s,
        )
