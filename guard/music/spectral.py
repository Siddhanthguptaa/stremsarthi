"""Fallback music scorer used only when the YAMNet weights are missing or fail their SHA-256 check.

An energy and spectral heuristic: music holds notes steady for hundreds of milliseconds, so its
spectral peaks persist from frame to frame, while speech glides. The score is the fraction of
spectral peaks that are still peaks 190 ms later, squashed to 0..1. It is much weaker than YAMNet
(see the README for measured numbers) and is meant to keep a rough music warning alive, nothing more.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import maximum_filter, uniform_filter1d

SR = 16_000
WINDOW = 15_600  # same window and hop as YAMNet so the state machine is shared
HOP = 7_680
_NFFT = 1024
_STEP = 256
_LAG = 12  # frames, 192 ms
_WIN = np.hanning(_NFFT).astype(np.float32)
# Squash parameters, set on seeded synthetic songs versus TTS speech (see README).
_P0 = 0.45
_SLOPE = 12.0


def persistence(window: np.ndarray) -> float:
    x = np.asarray(window, dtype=np.float32)
    if len(x) < _NFFT + _STEP * (_LAG + 4) or float(np.max(np.abs(x))) < 1e-4:
        return 0.0
    frames = sliding_window_view(x, _NFFT)[::_STEP]
    mag = np.abs(np.fft.rfft(frames * _WIN, axis=1))
    lg = 20.0 * np.log10(mag + 1e-4)
    lg = lg - uniform_filter1d(lg, size=41, axis=1, mode="nearest")
    lo, hi = 8, 256  # about 125 Hz to 4 kHz
    peak = (lg == maximum_filter(lg, size=(1, 9), mode="nearest")) & (lg > 12.0)
    peak[:, :lo] = False
    peak[:, hi:] = False
    grown = maximum_filter(peak.astype(np.uint8), size=(1, 3), mode="nearest").astype(bool)
    a, b = peak[:-_LAG], grown[_LAG:]
    n = int(a.sum())
    if n < 20:
        return 0.0
    return float((a & b).sum()) / n


def music_score(window: np.ndarray) -> float:
    p = persistence(window)
    return float(1.0 / (1.0 + np.exp(-_SLOPE * (p - _P0))))


class SpectralScorer:
    """Same interface as ``YamnetScorer``: ``score(samples)`` gives P(music) per complete window."""

    backend = "spectral-fallback"

    def score(self, samples: np.ndarray) -> np.ndarray:
        n = len(samples)
        if n < WINDOW:
            return np.zeros(0, dtype=np.float32)
        k = (n - WINDOW) // HOP + 1
        return np.array(
            [music_score(samples[i * HOP : i * HOP + WINDOW]) for i in range(k)], dtype=np.float32
        )
