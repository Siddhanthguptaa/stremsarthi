"""Groq Whisper (``whisper-large-v3-turbo``) over REST with word timestamps.

Groq allows 20 requests per minute and bills anything under 10 s as 10 s, so the adapter holds a token
bucket at ``rpm`` (default 18) and insists on chunks of at least ``min_chunk_s`` (3 s). ``GroqChunker``
merges consecutive segments into such chunks while keeping every sample on the source clock (gaps are
zero filled, the overlap of continuation segments is dropped).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
import numpy as np

from audio.stt.base import (
    SAMPLE_RATE,
    SttProviderError,
    SttRateLimited,
    SttUnavailable,
    TokenBucket,
    interpolate_words,
    normalise_lang,
    pcm_duration_s,
    pcm_to_wav,
    raise_for_http_status,
)
from contracts.config import Config
from contracts.speech import Transcript, Word

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


class GroqSTT:
    name: ClassVar[str] = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = GROQ_MODEL,
        timeout_s: float = 1.5,
        rpm: float = 18.0,
        burst: int = 2,
        min_chunk_s: float = 3.0,
        base_url: str = GROQ_URL,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")
        self.model = model
        self.timeout_s = timeout_s
        self.min_chunk_s = min_chunk_s
        self.base_url = base_url
        self._client = client
        self._owns_client = client is None
        self._clock = clock
        self.bucket = TokenBucket(rpm, burst, clock)

    @classmethod
    def from_config(cls, config: Config, **kwargs: Any) -> GroqSTT:
        key = config.groq_api_key.get_secret_value() if config.groq_api_key else ""
        kwargs.setdefault("rpm", float(config.audio.groq_rpm))
        return cls(key, **kwargs)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Transcript:
        if not self._key:
            raise SttUnavailable("GROQ_API_KEY is not set")
        dur = pcm_duration_s(pcm16k)
        if dur + 1e-6 < self.min_chunk_s:
            raise ValueError(f"Groq chunks must be at least {self.min_chunk_s:.1f} s (use GroqChunker)")
        if not self.bucket.try_acquire():
            raise SttRateLimited("local token bucket empty", self.bucket.retry_after_s())

        data: dict[str, Any] = {
            "model": self.model,
            "response_format": "verbose_json",
            "temperature": "0",
            "timestamp_granularities[]": ["word"],
        }
        lang = normalise_lang(lang_hint, default="")
        if lang and lang_hint not in ("hinglish", "unknown", "auto"):
            data["language"] = lang
        files = {"file": ("chunk.wav", pcm_to_wav(pcm16k, SAMPLE_RATE), "audio/wav")}
        t_start = self._clock()
        try:
            resp = await asyncio.wait_for(
                self._http().post(
                    self.base_url,
                    data=data,
                    files=files,
                    headers={"Authorization": f"Bearer {self._key}"},
                    timeout=self.timeout_s,
                ),
                timeout=self.timeout_s,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise TimeoutError("groq stt timed out") from exc
        except httpx.HTTPError as exc:
            raise SttProviderError(f"groq transport error ({type(exc).__name__})") from None
        raise_for_http_status(resp.status_code, resp.headers.get("retry-after"))
        try:
            body = resp.json()
            text = str(body.get("text") or "").strip()
            raw_words = body.get("words") or []
        except (ValueError, AttributeError):
            raise SttProviderError("groq returned an unparsable body", resp.status_code) from None

        pts_end = pts_start + dur
        words = self._words(text, raw_words, pts_start, pts_end)
        return Transcript(
            pts_start=pts_start,
            pts_end=pts_end,
            text=text,
            words=words,
            lang=normalise_lang(body.get("language"), default=normalise_lang(lang_hint)),
            provider="groq",
            latency_s=self._clock() - t_start,
        )

    @staticmethod
    def _words(text: str, raw: Any, pts_start: float, pts_end: float) -> tuple[Word, ...]:
        out: list[Word] = []
        try:
            for w in raw:
                token = str(w["word"]).strip()
                if not token:
                    continue
                t0 = min(max(pts_start + float(w["start"]), pts_start), pts_end)
                t1 = min(max(pts_start + float(w["end"]), t0), pts_end)
                out.append(Word(token, t0, t1, False, 0))
        except (TypeError, KeyError, ValueError):
            out = []
        if out:
            return tuple(out)
        return interpolate_words(text, pts_start, pts_end)


@dataclass(slots=True)
class GroqChunker:
    """Accumulates segments into chunks of at least ``min_s`` seconds on the source clock.

    ``add`` returns ``(pcm, pts_start)`` once enough audio is held, otherwise ``None``. A pause longer than
    ``max_gap_s`` between segments starts a new chunk (the held audio is returned from ``add`` even if
    shorter than ``min_s``, because waiting would only add delay). ``flush`` returns whatever is held.
    """

    min_s: float = 3.0
    max_gap_s: float = 1.0
    max_s: float = 25.0
    _pcm: list[np.ndarray] = field(default_factory=list)
    _start: float = 0.0
    _n: int = 0

    @property
    def held_s(self) -> float:
        return self._n / SAMPLE_RATE

    def _take(self) -> tuple[np.ndarray, float] | None:
        if not self._n:
            return None
        pcm = np.concatenate(self._pcm)
        start = self._start
        self._pcm, self._n = [], 0
        return pcm, start

    def flush(self) -> tuple[np.ndarray, float] | None:
        return self._take()

    def add(self, pcm: Any, pts_start: float) -> tuple[np.ndarray, float] | None:
        a = np.asarray(pcm, dtype=np.int16)
        if not self._n:
            self._pcm, self._n, self._start = [a], len(a), pts_start
        else:
            end = self._start + self._n / SAMPLE_RATE
            gap = pts_start - end
            if gap > self.max_gap_s:
                ready = self._take()
                self._pcm, self._n, self._start = [a], len(a), pts_start
                return ready
            if gap >= 0:
                fill = int(round(gap * SAMPLE_RATE))
                if fill:
                    self._pcm.append(np.zeros(fill, dtype=np.int16))
                    self._n += fill
                self._pcm.append(a)
                self._n += len(a)
            else:  # overlap of a continuation segment: keep only the new samples
                skip = int(round(-gap * SAMPLE_RATE))
                if skip < len(a):
                    self._pcm.append(a[skip:])
                    self._n += len(a) - skip
        if self.held_s >= self.min_s or self.held_s >= self.max_s:
            return self._take()
        return None
