"""Shared pieces of the STT adapters: protocol re-export, errors, token bucket, audio and word helpers."""

from __future__ import annotations

import io
import time
import wave
from collections.abc import Callable
from typing import Any

import numpy as np

from contracts.protocols import SpeechToText
from contracts.speech import Transcript, Word

__all__ = [
    "SttBusy",
    "SttError",
    "SttProviderError",
    "SttRateLimited",
    "SttUnavailable",
    "SpeechToText",
    "TokenBucket",
    "Transcript",
    "Word",
    "interpolate_words",
    "normalise_lang",
    "pcm_duration_s",
    "pcm_to_float32",
    "pcm_to_wav",
    "with_seg_id",
]

SAMPLE_RATE = 16_000


class SttError(RuntimeError):
    """Base class of provider failures. Messages never contain audio, transcript text or keys."""


class SttUnavailable(SttError):
    """The engine cannot run at all (no API key, model files missing)."""


class SttRateLimited(SttError):
    """Local token bucket empty or the provider answered 429."""

    def __init__(self, message: str = "rate limited", retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class SttProviderError(SttError):
    """Provider answered with an error status or an unusable body."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SttBusy(SttError):
    """The local engine is still working on earlier segments: shed load instead of queueing."""


def raise_for_http_status(status: int, retry_after: str | None = None) -> None:
    """Map an HTTP status to an STT error (no body text is ever included)."""
    if 200 <= status < 300:
        return
    if status == 429:
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None
        raise SttRateLimited("provider rate limit (429)", wait)
    raise SttProviderError(f"provider error (HTTP {status})", status)


class TokenBucket:
    """Non-blocking token bucket (``rate_per_min`` refill, ``burst`` capacity). Never sleeps."""

    def __init__(
        self, rate_per_min: float, burst: int = 1, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate_per_min <= 0 or burst < 1:
            raise ValueError("rate_per_min must be > 0 and burst >= 1")
        self.rate_per_s = rate_per_min / 60.0
        self.capacity = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_s)
        self._last = now

    def try_acquire(self, n: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def retry_after_s(self, n: float = 1.0) -> float:
        self._refill()
        return max(0.0, (n - self._tokens) / self.rate_per_s)


# --------------------------------------------------------------------------- audio helpers
def pcm_to_float32(pcm: Any) -> np.ndarray:
    """int16 PCM (or float audio already in -1..1) to float32 -1..1."""
    a = np.asarray(pcm)
    if a.dtype == np.int16:
        return a.astype(np.float32) / 32768.0
    return a.astype(np.float32, copy=False)


def pcm_to_int16(pcm: Any) -> np.ndarray:
    a = np.asarray(pcm)
    if a.dtype == np.int16:
        return a
    return np.clip(a * 32768.0, -32768, 32767).astype(np.int16)


def pcm_duration_s(pcm: Any, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / float(sample_rate)


def pcm_to_wav(pcm: Any, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Mono 16-bit WAV container for REST upload."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_to_int16(pcm).tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------- text and word helpers
def interpolate_words(text: str, t0: float, t1: float, seg_id: int = 0) -> tuple[Word, ...]:
    """Spread the words of ``text`` uniformly over ``[t0, t1]``; all flagged ``approx``."""
    tokens = text.split()
    if not tokens:
        return ()
    step = max(t1 - t0, 0.0) / len(tokens)
    return tuple(
        Word(tok, t0 + i * step, t0 + (i + 1) * step if i < len(tokens) - 1 else max(t1, t0), True, seg_id)
        for i, tok in enumerate(tokens)
    )


_LANG_NAMES = {
    "hindi": "hi",
    "english": "en",
    "kannada": "kn",
    "tamil": "ta",
    "telugu": "te",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu",
    "malayalam": "ml",
    "punjabi": "pa",
    "urdu": "ur",
    "odia": "or",
    "oriya": "or",
    "assamese": "as",
    "nepali": "ne",
}


def normalise_lang(code: str | None, default: str = "unknown") -> str:
    """'hi-IN' -> 'hi', 'Hindi' -> 'hi'. Output always matches the contract's code pattern."""
    if not code:
        return default
    c = code.strip().lower()
    if c in _LANG_NAMES:
        return _LANG_NAMES[c]
    c = c.split("-")[0].split("_")[0]
    return c if c.isalnum() and 1 <= len(c) <= 8 else default


def with_seg_id(tr: Transcript, seg_id: int) -> Transcript:
    """Copy of ``tr`` whose words carry ``seg_id`` (the adapters do not know it)."""
    words = tuple(Word(w.text, w.t0, w.t1, w.approx, seg_id) for w in tr.words)
    return Transcript(tr.pts_start, tr.pts_end, tr.text, words, tr.lang, tr.provider, tr.latency_s)
