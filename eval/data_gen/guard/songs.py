"""Seeded synthetic songs for the eval set. Written for the eval harness only; it shares no code with
``guard.music.synth`` (the product's own demo songs), so a bug there cannot make the eval agree with itself.

A song is a pad chord progression, a saw-ish lead playing a seeded motif, a bass, and a kick/hat groove.
Pure numpy, float64 internally, returned as float32 16 kHz mono with peak 0.8. No sample of real music is used.
"""

from __future__ import annotations

import numpy as np

SR = 16_000
_SCALES = ((0, 2, 3, 5, 7, 8, 10), (0, 2, 4, 5, 7, 9, 11), (0, 2, 3, 5, 7, 9, 10), (0, 3, 5, 7, 10, 12, 15))


def _hz(m: float) -> float:
    return 440.0 * 2.0 ** ((m - 69.0) / 12.0)


def _voice(f: float, n: int, bright: int, decay: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    out = np.zeros(n)
    det = 1.0 + rng.uniform(-0.004, 0.004)
    for h in range(1, bright + 1):
        if f * h * det > SR / 2 - 400:
            break
        out += np.sin(2 * np.pi * f * h * det * t + rng.uniform(0, 6.28)) / h
    env = np.minimum(1.0, t / 0.012) * np.exp(-decay * t)
    return out * env


def _put(dst: np.ndarray, src: np.ndarray, at: int, gain: float) -> None:
    if 0 <= at < len(dst):
        k = min(len(src), len(dst) - at)
        dst[at : at + k] += gain * src[:k]


def make_song(seed: int, seconds: float = 60.0) -> np.ndarray:
    rng = np.random.default_rng(10_000 + seed)
    n = int(seconds * SR)
    out = np.zeros(n + SR)
    bpm = float(rng.uniform(78, 138))
    beat = 60.0 / bpm
    scale = _SCALES[int(rng.integers(0, len(_SCALES)))]
    root = int(rng.integers(40, 52))
    prog = [int(x) for x in rng.integers(0, 6, 4)]
    bright = int(rng.integers(3, 10))
    bar_s = 4 * beat
    motif = [int(x) for x in rng.integers(0, 9, 8)]
    kick_t = np.arange(int(0.22 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(45.0 + 120.0 * np.exp(-kick_t * 30.0)) / SR) * np.exp(-kick_t * 10.0)
    hat = np.diff(rng.standard_normal(int(0.04 * SR)), prepend=0.0) * np.exp(
        -np.arange(int(0.04 * SR)) / SR * 110.0
    )
    for bar in range(int(seconds / bar_s) + 2):
        t0 = bar * bar_s
        if t0 >= seconds:
            break
        if bar % 6 == 0:  # drift the motif so stretches of a song do not repeat exactly
            motif = [(m + int(rng.integers(-2, 3))) % 9 for m in motif]
        deg = prog[bar % 4]
        for k in (0, 2, 4):
            midi = root + 12 + scale[(deg + k) % 7] + 12 * ((deg + k) // 7)
            _put(out, _voice(_hz(midi), int(bar_s * SR), 4, 0.5, rng), int(t0 * SR), 0.10)
        for b in (0, 2):
            _put(
                out,
                _voice(_hz(root - 12 + scale[deg % 7]), int(beat * 1.5 * SR), 2, 2.4, rng),
                int((t0 + b * beat) * SR),
                0.5,
            )
        for i, d in enumerate(motif):
            midi = root + 24 + scale[d % 7] + 12 * (d // 7)
            _put(
                out,
                _voice(_hz(midi), int(beat * 0.9 * SR), bright, 3.0, rng),
                int((t0 + i * beat / 2) * SR),
                0.26,
            )
        for b in range(4):
            if b % 2 == 0:
                _put(out, kick, int((t0 + b * beat) * SR), 0.7)
            _put(out, hat, int((t0 + b * beat) * SR), 0.2)
            _put(out, hat, int((t0 + (b + 0.5) * beat) * SR), 0.12)
    out = out[:n]
    return (0.8 * out / max(1e-9, float(np.max(np.abs(out))))).astype(np.float32)
