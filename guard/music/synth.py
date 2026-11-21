"""Seeded synthetic "songs" for the demo fingerprint library, tests and evaluation.

Everything here is generated from numbers (no copyrighted audio, no recordings). A song is a
chord pad, a bass line, a plucked melody on a scale and a drum pattern, with a new melody every
eight bars so that no two stretches of a track repeat exactly. These are SYNTHETIC: they stand in
for real music in unit tests, and say nothing about accuracy on real songs.
"""

from __future__ import annotations

import numpy as np

SR = 16_000
_MAJOR = (0, 2, 4, 5, 7, 9, 11)
_MINOR = (0, 2, 3, 5, 7, 8, 10)
_PROGRESSIONS = ((0, 4, 5, 3), (0, 5, 3, 4), (5, 3, 0, 4), (0, 3, 4, 3), (1, 4, 0, 5), (0, 2, 3, 4))


def _hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def _tone(
    freq: float, n: int, harmonics: int, decay: float, attack: float, vibrato: float, rng: np.random.Generator
) -> np.ndarray:
    t = np.arange(n) / SR
    phase = 2 * np.pi * freq * t + vibrato * np.sin(2 * np.pi * 5.0 * t + rng.uniform(0, 6.28))
    out = np.zeros(n)
    for h in range(1, harmonics + 1):
        if freq * h > SR / 2 - 500:
            break
        out += np.sin(h * phase) / (h**1.1)
    env = np.exp(-decay * t) * np.minimum(1.0, t / max(attack, 1e-4))
    release = min(n, int(0.02 * SR))
    if release > 0:
        env[-release:] *= np.linspace(1.0, 0.0, release)
    return out * env


def _kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    f = 50.0 + 110.0 * np.exp(-t * 28.0)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9.0)


def _noise_hit(n: int, decay: float, tone: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0.0)  # tilt towards the highs
    out = 0.6 * noise * np.exp(-t * decay)
    if tone:
        out += 0.5 * np.sin(2 * np.pi * tone * t) * np.exp(-t * decay * 0.7)
    return out


def _add(dst: np.ndarray, src: np.ndarray, start: int, gain: float) -> None:
    if start >= len(dst) or start < 0:
        return
    end = min(len(dst), start + len(src))
    dst[start:end] += gain * src[: end - start]


def synth_song(seed: int, seconds: float = 60.0, tempo: float | None = None) -> np.ndarray:
    """A deterministic float32 mono song at 16 kHz, peak about 0.8."""
    rng = np.random.default_rng(seed)
    total = int(seconds * SR)
    out = np.zeros(total + SR)
    bpm = float(tempo if tempo is not None else rng.uniform(84, 132))
    beat = 60.0 / bpm
    scale = _MINOR if rng.random() < 0.5 else _MAJOR
    root = int(rng.integers(43, 56))
    progression = _PROGRESSIONS[int(rng.integers(0, len(_PROGRESSIONS)))]
    swing_drums = rng.random() < 0.5
    harmonics = int(rng.integers(4, 9))
    bars = int(seconds / (4 * beat)) + 2
    melody_rng = np.random.default_rng(seed * 7 + 1)
    degree = int(melody_rng.integers(0, 7))
    kick, snare, hat = (
        _kick(int(0.25 * SR)),
        _noise_hit(int(0.18 * SR), 22.0, 190.0, rng),
        _noise_hit(int(0.05 * SR), 90.0, 0.0, rng),
    )
    for bar in range(bars):
        t0 = bar * 4 * beat
        if t0 >= seconds:
            break
        if bar % 8 == 0:  # new melodic material every eight bars
            melody_rng = np.random.default_rng(seed * 7 + 1 + bar)
        chord_degree = progression[bar % len(progression)]
        chord = [
            root + 12 + scale[(chord_degree + k) % 7] + 12 * ((chord_degree + k) // 7) for k in (0, 2, 4)
        ]
        bar_n = int(4 * beat * SR)
        for note in chord:
            _add(out, _tone(_hz(note), bar_n, 5, 0.6, 0.08, 0.0, rng), int(t0 * SR), 0.12)
        bass = root - 12 + scale[chord_degree % 7]
        for b in (0, 2):
            _add(
                out,
                _tone(_hz(bass), int(beat * 1.6 * SR), 3, 2.2, 0.01, 0.0, rng),
                int((t0 + b * beat) * SR),
                0.45,
            )
        for step in range(8):  # eighth-note melody
            if melody_rng.random() < 0.22:
                continue
            degree = int(np.clip(degree + int(melody_rng.integers(-2, 3)), -2, 9))
            midi = root + 24 + scale[degree % 7] + 12 * (degree // 7)
            dur = int(beat * 0.5 * 1.8 * SR)
            _add(
                out,
                _tone(_hz(midi), dur, harmonics, 3.2, 0.006, 0.8, rng),
                int((t0 + step * beat * 0.5) * SR),
                0.28,
            )
        for b in range(4):  # drums
            tb = int((t0 + b * beat) * SR)
            if b in (0, 2) or (swing_drums and b == 3 and bar % 2):
                _add(out, kick, tb, 0.7)
            if b in (1, 3):
                _add(out, snare, tb, 0.35)
            _add(out, hat, tb, 0.18)
            _add(out, hat, tb + int(beat * 0.5 * SR), 0.12)
    out = out[:total]
    return (0.8 * out / max(1e-9, float(np.max(np.abs(out))))).astype(np.float32)
