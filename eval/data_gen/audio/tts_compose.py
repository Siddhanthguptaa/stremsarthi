"""Offline TTS engines and the composer that places words on a timeline (ground truth by construction).

Engines (only what is installed offline; nothing is downloaded, no key is used):

* ``flite``  ffmpeg's ``flite`` source filter (``--enable-libflite``), five English voices (awb, kal16, rms, slt,
  kal). English phonemiser only: Hindi/Hinglish and regional words are respelled in ``Tok.tts`` and come out as
  English-accented approximations. espeak-ng (which has hi/kn/ta/te/bn voices) is NOT installed and Sarvam TTS
  needs a key, so neither is used. Gap, in the report: no native Kannada/Tamil/Telugu/Bengali phonetics.
* ``tone``   deterministic formant-like synthetic "speech" (no dependencies): used by unit tests and as the
  fallback when ffmpeg has no flite. The energy VAD treats it as speech; it carries no linguistic content.

A word is synthesised alone, trimmed to its voiced part and placed on the timeline, so its ``t0``/``t1`` are
exact sample positions. Nothing here reads any recogniser output.
"""

from __future__ import annotations

import hashlib
import random
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from .numbers import Tok
from .truth import WordTruth

SR = 16_000
FLITE_VOICES = ("awb", "kal16", "rms", "slt")
TONE_VOICES = ("t0", "t1", "t2", "t3")
PEAK = 0.5 * 32767.0
EDGE_S = 0.004


def _trim_and_level(x: np.ndarray) -> np.ndarray:
    """Cut leading/trailing silence, 4 ms fades, peak level to -6 dBFS (all voices equally loud)."""
    if len(x) == 0:
        return x
    f = x.astype(np.float64)
    peak = float(np.max(np.abs(f)))
    if peak < 1.0:
        return np.zeros(0, dtype=np.int16)
    idx = np.flatnonzero(np.abs(f) > 0.02 * peak)
    if len(idx) == 0:
        return np.zeros(0, dtype=np.int16)
    f = f[idx[0] : idx[-1] + 1] * (PEAK / peak)
    k = min(len(f) // 2, int(EDGE_S * SR))
    if k:
        ramp = np.linspace(0.0, 1.0, k)
        f[:k] *= ramp
        f[-k:] *= ramp[::-1]
    return np.rint(f).astype(np.int16)


def flite_available() -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return " flite " in out.stdout


class FliteEngine:
    name = "flite"
    voices = FLITE_VOICES

    def __init__(self, cache_path: Path | None = None, workers: int = 4) -> None:
        self.cache: dict[tuple[str, str], np.ndarray] = {}
        self.cache_path = cache_path
        self.workers = workers
        if cache_path is not None and cache_path.exists():
            with np.load(cache_path, allow_pickle=False) as z:
                for key in z.files:
                    voice, _, text = key.partition("|")
                    self.cache[(voice, text)] = z[key]

    def _synth(self, tts: str, voice: str) -> np.ndarray:
        # arguments go as a list (no shell); words are alphanumeric so the filtergraph needs no escaping
        text = "".join(c for c in tts if c.isalnum() or c == " ").strip()
        graph = f"flite=text='{text}':voice={voice}"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", graph]
            + ["-ar", str(SR), "-ac", "1", "-f", "s16le", "-"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError("flite synthesis failed")
        return _trim_and_level(np.frombuffer(proc.stdout, dtype=np.int16))

    def prewarm(self, items: Iterable[tuple[str, str]]) -> None:
        todo = sorted({(v, t) for v, t in items if (v, t) not in self.cache})
        if not todo:
            return
        with ThreadPoolExecutor(self.workers) as pool:
            for (v, t), arr in zip(todo, pool.map(lambda vt: self._synth(vt[1], vt[0]), todo), strict=True):
                self.cache[(v, t)] = arr
        if self.cache_path is not None:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.cache_path, **{f"{v}|{t}": a for (v, t), a in sorted(self.cache.items())})

    def word(self, tts: str, voice: str) -> np.ndarray:
        key = (voice, tts)
        if key not in self.cache:
            self.cache[key] = self._synth(tts, voice)
        return self.cache[key]


class ToneEngine:
    """Deterministic formant-like bursts: pitch and length depend on the text and the voice, nothing else."""

    name = "tone"
    voices = TONE_VOICES

    def __init__(self) -> None:
        self.cache: dict[tuple[str, str], np.ndarray] = {}

    def prewarm(self, items: Iterable[tuple[str, str]]) -> None:  # symmetry with FliteEngine
        for v, t in items:
            self.word(t, v)

    def word(self, tts: str, voice: str) -> np.ndarray:
        key = (voice, tts)
        if key in self.cache:
            return self.cache[key]
        h = int.from_bytes(hashlib.sha256(f"{voice}|{tts}".encode()).digest()[:8], "big")
        rng = np.random.default_rng(h)
        dur = min(0.9, 0.10 + 0.045 * len(tts.replace(" ", "")))
        n = int(dur * SR)
        t = np.arange(n) / SR
        base = 110.0 + 25.0 * TONE_VOICES.index(voice) + (h % 60)
        f0 = base * (1.0 + 0.08 * np.sin(2 * np.pi * (2.5 + (h >> 8) % 3) * t))
        phase = 2 * np.pi * np.cumsum(f0) / SR
        x = np.sin(phase) + 0.5 * np.sin(2 * phase) + 0.3 * np.sin(3 * phase) + 0.1 * rng.standard_normal(n)
        env = np.minimum(1.0, np.minimum(t / 0.02, (dur - t) / 0.03))
        self.cache[key] = _trim_and_level(np.rint(x * env * 9000).astype(np.int16))
        return self.cache[key]


def make_engine(kind: str = "auto", cache_path: Path | None = None) -> FliteEngine | ToneEngine:
    if kind == "tone":
        return ToneEngine()
    if kind in ("flite", "auto"):
        if flite_available():
            return FliteEngine(cache_path)
        if kind == "flite":
            raise RuntimeError("ffmpeg has no flite filter")
        return ToneEngine()
    raise ValueError(f"unknown TTS engine: {kind}")


def stretch(x: np.ndarray, speed: float) -> np.ndarray:
    if abs(speed - 1.0) < 1e-6 or len(x) == 0:
        return x
    down = int(round(speed * 100))
    y = resample_poly(x.astype(np.float64), 100, down)
    return np.rint(y).astype(np.int16)


class Composer:
    """Builds one clip: words placed back to back with pauses; every word keeps its exact start and end."""

    def __init__(self, engine: FliteEngine | ToneEngine, voice: str, speed: float, rng: random.Random) -> None:
        self.engine, self.voice, self.speed, self.rng = engine, voice, speed, rng
        self.chunks: list[np.ndarray] = []
        self.pos = 0
        self.words: list[WordTruth] = []
        self.digit_gap: tuple[float, float] = (0.05, 0.13)  # pause range between spoken digits
        self._stretched: dict[str, np.ndarray] = {}

    @property
    def now(self) -> float:
        return self.pos / SR

    def pause(self, seconds: float) -> None:
        n = int(round(seconds * SR))
        if n > 0:
            self.chunks.append(np.zeros(n, dtype=np.int16))
            self.pos += n

    def _word(self, tts: str) -> np.ndarray:
        if tts not in self._stretched:
            self._stretched[tts] = stretch(self.engine.word(tts, self.voice), self.speed)
        return self._stretched[tts]

    def say(
        self,
        toks: list[Tok],
        *,
        kind: str | None = None,
        span: int | None = None,
        hard_neg: bool = False,
        gap: tuple[float, float] = (0.05, 0.13),
        group_gap: tuple[float, float] = (0.14, 0.30),
        groups: tuple[int, ...] = (),
    ) -> tuple[int, int]:
        """Place ``toks``; return the (first, last) index of the new words in ``self.words``.

        ``groups`` are token counts after which a longer pause (reading a number in chunks) is inserted."""
        first = len(self.words)
        marks = set()
        total = 0
        for g in groups[:-1]:
            total += g
            marks.add(total)
        for i, tok in enumerate(toks):
            if i:
                lo, hi = group_gap if i in marks else gap
                self.pause(self.rng.uniform(lo, hi))
            w = self._word(tok.tts)
            if len(w) == 0:
                continue
            t0 = self.pos / SR
            self.chunks.append(w)
            self.pos += len(w)
            self.words.append(WordTruth(tok.stt, tok.tts, t0, self.pos / SR, kind, tok.digit, span, hard_neg))
        return first, len(self.words) - 1

    def pcm(self, tail_s: float = 1.0) -> np.ndarray:
        self.pause(tail_s)
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.int16)
