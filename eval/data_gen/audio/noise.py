"""Seeded background noise and music beds, mixed at a target speech-to-noise ratio. Pure numpy, no files."""

from __future__ import annotations

import numpy as np

SR = 16_000


def white(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n)


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise by shaping white noise in the frequency domain."""
    x = np.fft.rfft(rng.standard_normal(n))
    f = np.arange(len(x), dtype=np.float64)
    f[0] = 1.0
    x /= np.sqrt(f)
    out = np.fft.irfft(x, n)
    return out / max(1e-9, float(np.std(out)))


_CHORDS = ((261.63, 329.63, 392.0), (220.0, 261.63, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63))


def music(n: int, rng: np.random.Generator) -> np.ndarray:
    """A slow chord progression with a plucked arpeggio: tonal, steady, clearly not speech."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    beat = 0.5
    shift = float(rng.uniform(0.9, 1.15))
    for i in range(int(n / SR / beat) + 1):
        a, b = int(i * beat * SR), min(n, int((i + 1) * beat * SR))
        if a >= n:
            break
        chord = _CHORDS[(i // 4) % len(_CHORDS)]
        f = chord[i % 3] * shift
        env = np.exp(-4.0 * (t[a:b] - t[a]))
        out[a:b] += env * np.sin(2 * np.pi * f * t[a:b]) + 0.4 * env * np.sin(2 * np.pi * 2 * f * t[a:b])
        for g in chord:
            out[a:b] += 0.15 * np.sin(2 * np.pi * g * shift * t[a:b])
    return out / max(1e-9, float(np.std(out)))


def babble(n: int, bits: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Overlapped fragments of other speech (from the word cache) as cafe-style background talk."""
    out = np.zeros(n)
    if not bits:
        return pink(n, rng)
    for _ in range(3):  # three talkers
        pos = int(rng.integers(0, 4000))
        while pos < n:
            w = bits[int(rng.integers(0, len(bits)))].astype(np.float64)
            m = min(len(w), n - pos)
            out[pos : pos + m] += w[:m]
            pos += len(w) + int(rng.integers(1000, 6000))
    return out / max(1e-9, float(np.std(out)))


def active_rms(x: np.ndarray, thresh_db: float = -45.0) -> float:
    """RMS of the louder frames only (silence between words must not lower the reference level)."""
    hop = 320
    n = len(x) // hop
    if n == 0:
        return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)
    frames = x[: n * hop].astype(np.float64).reshape(n, hop)
    rms = np.sqrt((frames**2).mean(axis=1))
    peak = rms.max() + 1e-9
    keep = rms > peak * 10 ** (thresh_db / 20.0)
    return float(np.sqrt((rms[keep] ** 2).mean()) + 1e-9)


def mix(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """``speech`` (int16) plus ``noise`` scaled so the speech/noise ratio is ``snr_db``; returns int16 (peak-limited)."""
    s = speech.astype(np.float64)
    g = active_rms(s) / (10 ** (snr_db / 20.0))
    y = s + noise[: len(s)] * g
    peak = float(np.max(np.abs(y))) or 1.0
    if peak > 32000.0:
        y *= 32000.0 / peak
    return np.rint(y).astype(np.int16)
