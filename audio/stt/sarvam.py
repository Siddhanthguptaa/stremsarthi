"""Sarvam speech-to-text over REST, one request per segment.

``POST https://api.sarvam.ai/speech-to-text`` (multipart: ``file``, ``model``, ``mode``,
``language_code``, ``with_timestamps``; header ``api-subscription-key``). The REST limit is 60 requests
per minute and 30 s of audio, so a token bucket at 55/min sheds load locally (the race then falls
back) instead of earning a 429. The documented REST timestamps are chunk level (sentence or phrase), never
word level, so words are always ``approx`` (interpolated). Live behaviour (latency, whether ``mode`` is
honoured by ``saaras:v4``) is UNVERIFIED until the spike runs with a key.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any, ClassVar

import httpx

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

SARVAM_URL = "https://api.sarvam.ai/speech-to-text"
MAX_AUDIO_S = 30.0
_SARVAM_LANGS = {
    "hi": "hi-IN",
    "en": "en-IN",
    "kn": "kn-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "bn": "bn-IN",
    "mr": "mr-IN",
    "gu": "gu-IN",
    "ml": "ml-IN",
    "pa": "pa-IN",
    "or": "od-IN",
    "od": "od-IN",
    "ur": "ur-IN",
    "as": "as-IN",
    "ne": "ne-IN",
}


def sarvam_language_code(hint: str | None) -> str:
    """Map a hint ('hi', 'hi-IN', None, 'hinglish') to Sarvam's BCP-47 code or 'unknown'."""
    if not hint:
        return "unknown"
    h = hint.strip().lower()
    if h in ("hinglish", "unknown", "auto", "codemix"):
        return "unknown"
    if "-" in h and h.endswith("-in"):
        return h.split("-")[0] + "-IN"
    return _SARVAM_LANGS.get(normalise_lang(h, default=""), "unknown")


class SarvamSTT:
    name: ClassVar[str] = "sarvam"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "saaras:v4",
        mode: str | None = "codemix",
        timeout_s: float = 1.8,
        rpm: float = 55.0,
        burst: int = 4,
        with_timestamps: bool = False,
        base_url: str = SARVAM_URL,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = api_key if api_key is not None else os.environ.get("SARVAM_API_KEY", "")
        self.model = model
        self.mode = mode
        self.timeout_s = timeout_s
        self.with_timestamps = with_timestamps
        self.base_url = base_url
        self._client = client
        self._owns_client = client is None
        self._clock = clock
        self.bucket = TokenBucket(rpm, burst, clock)

    @classmethod
    def from_config(cls, config: Config, **kwargs: Any) -> SarvamSTT:
        key = config.sarvam_api_key.get_secret_value() if config.sarvam_api_key else ""
        return cls(key, **kwargs)

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def transcribe(self, pcm16k: Any, pts_start: float, lang_hint: str | None) -> Transcript:
        if not self._key:
            raise SttUnavailable("SARVAM_API_KEY is not set")
        dur = pcm_duration_s(pcm16k)
        if dur > MAX_AUDIO_S:
            raise ValueError("segment longer than the 30 s REST limit")
        if not self.bucket.try_acquire():
            raise SttRateLimited("local token bucket empty", self.bucket.retry_after_s())

        data = {
            "model": self.model,
            "language_code": sarvam_language_code(lang_hint),
            "with_timestamps": "true" if self.with_timestamps else "false",
        }
        if self.mode:
            data["mode"] = self.mode
        files = {"file": ("segment.wav", pcm_to_wav(pcm16k, SAMPLE_RATE), "audio/wav")}
        t_start = self._clock()
        try:
            resp = await asyncio.wait_for(
                self._http().post(
                    self.base_url,
                    data=data,
                    files=files,
                    headers={"api-subscription-key": self._key},
                    timeout=self.timeout_s,
                ),
                timeout=self.timeout_s,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise TimeoutError("sarvam stt timed out") from exc
        except httpx.HTTPError as exc:
            raise SttProviderError(f"sarvam transport error ({type(exc).__name__})") from None
        raise_for_http_status(resp.status_code, resp.headers.get("retry-after"))
        try:
            body = resp.json()
            text = str(body.get("transcript") or "").strip()
        except (ValueError, AttributeError):
            raise SttProviderError("sarvam returned an unparsable body", resp.status_code) from None

        pts_end = pts_start + dur
        words = self._words(text, body.get("timestamps"), pts_start, pts_end)
        return Transcript(
            pts_start=pts_start,
            pts_end=pts_end,
            text=text,
            words=words,
            lang=normalise_lang(body.get("language_code"), default=normalise_lang(lang_hint)),
            provider="sarvam",
            latency_s=self._clock() - t_start,
        )

    @staticmethod
    def _words(text: str, stamps: Any, pts_start: float, pts_end: float) -> tuple[Word, ...]:
        """Chunk-level timestamps (when present and consistent) bound the interpolation; else the segment."""
        if not text:
            return ()
        try:
            chunks = list(stamps["words"])
            starts = [float(x) for x in stamps["start_time_seconds"]]
            ends = [float(x) for x in stamps["end_time_seconds"]]
        except (TypeError, KeyError, ValueError):
            return interpolate_words(text, pts_start, pts_end)
        if not chunks or not (len(chunks) == len(starts) == len(ends)):
            return interpolate_words(text, pts_start, pts_end)
        words: list[Word] = []
        for chunk, s, e in zip(chunks, starts, ends, strict=True):
            lo = min(max(pts_start + s, pts_start), pts_end)
            hi = min(max(pts_start + e, lo), pts_end)
            words.extend(interpolate_words(str(chunk), lo, hi))
        return tuple(words) if words else interpolate_words(text, pts_start, pts_end)
