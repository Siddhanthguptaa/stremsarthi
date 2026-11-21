"""A looped WAV file as the sender's audio track (instead of the 1 kHz beep), for the demo reel.

``--audio-file reel.wav`` on ``python -m eval.sender.sender``. The file should be 48 kHz mono (what
``eval/data_gen/demo_audio.py`` writes); other rates are resampled and stereo is averaged, so a hand-made file
still works. The track loops when the video is longer than the audio. It has no beeps, so the stamp's beep flag
stays false and the delay reader's A/V offset is not available for such a stream (use the default beep for that).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .beep import SAMPLE_RATE


def load_track(path: Path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """float32 mono samples at `sample_rate`; raises ValueError for an empty file."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if sr != sample_rate:
        g = int(np.gcd(sr, sample_rate))
        mono = resample_poly(mono, sample_rate // g, sr // g).astype(np.float32)
    if mono.size == 0:
        raise ValueError(f"{path} holds no audio")
    return np.ascontiguousarray(np.clip(mono, -1.0, 1.0), dtype=np.float32)


class FileTrack:
    """Same ``frame_samples(index)`` interface as ``BeepTrack``, reading a looped file."""

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE, fps: int = 30) -> None:
        if sample_rate % fps:
            raise ValueError("sample rate must be a multiple of fps")
        self.sample_rate = sample_rate
        self.fps = fps
        self.samples_per_frame = sample_rate // fps
        self.samples = load_track(path, sample_rate)

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate

    def frame_samples(self, index: int) -> np.ndarray:
        n0 = (index * self.samples_per_frame) % len(self.samples)
        idx = (n0 + np.arange(self.samples_per_frame)) % len(self.samples)
        return self.samples[idx]
