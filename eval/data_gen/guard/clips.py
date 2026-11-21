"""Audio clips for the guard gates: speech (offline TTS, see ``eval/data_gen/audio/tts_compose``), music, mixes and
noisy fingerprint snippets. Everything is float32 16 kHz mono. Ground truth is by construction (we know what was
mixed where); no recogniser output is read."""

from __future__ import annotations

import random
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from ..audio.noise import active_rms, pink, white
from ..audio.numbers import Tok
from ..audio.tts_compose import Composer, FliteEngine, ToneEngine, make_engine

SR = 16_000
# plain English words the offline English TTS says well (the clips only need to be speech, not meaningful Hindi)
_SENTENCES = (
    "welcome back everyone today we play a new match with my friends",
    "thank you for the follow please like the stream and share it",
    "this map is very big so stay close to the team and watch the circle",
    "let us check the chat and answer a few questions before we start",
    "i will try the new weapon now and see how it feels in a real fight",
    "today the weather is good so the internet should be fine for the stream",
    "we have five minutes left in this round so push the middle now",
    "please wait while i change the settings and fix the microphone",
)


def speech(engine: FliteEngine | ToneEngine, seed: int, seconds: float) -> np.ndarray:
    """Continuous TTS speech of at least ``seconds`` (trimmed to it), float32 in -1..1, peak about 0.5."""
    rng = random.Random(seed)
    comp = Composer(engine, engine.voices[seed % len(engine.voices)], rng.uniform(0.95, 1.1), rng)
    while comp.now < seconds:
        comp.say([Tok(w, w) for w in rng.choice(_SENTENCES).split()], gap=(0.04, 0.10))
        comp.pause(rng.uniform(0.15, 0.4))
    pcm = comp.pcm(0.0)[: int(seconds * SR)]
    return (pcm.astype(np.float32) / 32768.0).astype(np.float32)


def _scale_to_snr(speech_x: np.ndarray, other: np.ndarray, snr_db: float) -> np.ndarray:
    g = active_rms(speech_x * 32768.0) / 32768.0 / (10 ** (snr_db / 20.0)) / max(active_rms(other), 1e-9)
    return (other * g).astype(np.float32)


def mix_speech_music(
    speech_x: np.ndarray, music: np.ndarray, snr_db: float, music_from_s: float
) -> np.ndarray:
    """Speech over the whole clip, music entering at ``music_from_s`` at ``snr_db`` (speech minus music, dB)."""
    n = len(speech_x)
    bed = np.zeros(n, dtype=np.float32)
    a = int(music_from_s * SR)
    bed[a:] = _scale_to_snr(speech_x, music[: n - a], snr_db)
    out = speech_x + bed
    peak = float(np.max(np.abs(out))) or 1.0
    return (out * min(1.0, 0.9 / peak)).astype(np.float32)


def add_noise(x: np.ndarray, kind: str, snr_db: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = pink(len(x), rng) if kind == "pink" else white(len(x), rng)
    out = x + _scale_to_snr(x, noise.astype(np.float32), snr_db)
    peak = float(np.max(np.abs(out))) or 1.0
    return (out * min(1.0, 0.9 / peak)).astype(np.float32)


def mp3_roundtrip(x: np.ndarray, kbps: int = 128) -> np.ndarray | None:
    """Encode and decode through MP3 (libmp3lame) with ffmpeg; None when ffmpeg cannot do it."""
    try:
        with tempfile.TemporaryDirectory() as d:
            src, mp3, back = Path(d, "a.wav"), Path(d, "a.mp3"), Path(d, "b.wav")
            wavfile.write(str(src), SR, np.rint(np.clip(x, -1, 1) * 32767).astype(np.int16))
            for cmd in (
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(src),
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    f"{kbps}k",
                    str(mp3),
                ],
                ["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", str(SR), "-ac", "1", str(back)],
            ):
                if subprocess.run(cmd, capture_output=True, timeout=60, check=False).returncode != 0:
                    return None
            _, y = wavfile.read(str(back))
        y = y.astype(np.float32) / 32768.0
        k = min(len(x), len(y))
        out = np.zeros(len(x), dtype=np.float32)
        out[:k] = y[:k]
        return out
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def tempo_change(x: np.ndarray, factor: float) -> np.ndarray:
    """Faster by ``factor`` (1.15 = 15 % faster, pitch moves with it: a documented fingerprint limit)."""
    down = int(round(factor * 100))
    return resample_poly(x.astype(np.float64), 100, down).astype(np.float32)


def write_wav(path: Path, x: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.rint(np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    wavfile.write(str(path), SR, pcm)
    import hashlib

    return hashlib.sha256(pcm.tobytes()).hexdigest()[:16]


def read_wav(path: Path) -> np.ndarray:
    sr, x = wavfile.read(str(path))
    if sr != SR:
        raise ValueError(f"{path.name}: sample rate {sr}, expected {SR}")
    return x.astype(np.float32) / 32768.0


__all__ = [
    "SR", "add_noise", "make_engine", "mix_speech_music", "mp3_roundtrip", "read_wav", "speech", "tempo_change", "write_wav",
]  # fmt: skip
