"""Audio beep track with known timestamps, and the reader-side onset detector.

A 1 kHz tone burst of 50 ms starts exactly on a frame boundary every 1.000 s (frame index % fps == 0),
at -12 dBFS peak. The matching stamp sets the beep flag on that frame.
"""

from __future__ import annotations

import math

import numpy as np

SAMPLE_RATE = 48000
TONE_HZ = 1000.0
BURST_S = 0.050
PERIOD_S = 1.0
LEVEL_DBFS = -12.0
RAMP_S = 0.001  # 1 ms raised-cosine edges, no clicks
ONSET_THRESHOLD_DBFS = -30.0
HOP_S = 0.005


def beep_frame_flag(index: int, fps: int = 30) -> bool:
    return index % int(round(fps * PERIOD_S)) == 0


class BeepTrack:
    """Deterministic audio: sample n carries the burst iff (n mod period) < burst length."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, fps: int = 30) -> None:
        if sample_rate % fps:
            raise ValueError("sample rate must be a multiple of fps")
        self.sample_rate = sample_rate
        self.fps = fps
        self.samples_per_frame = sample_rate // fps
        self.amp = 10.0 ** (LEVEL_DBFS / 20.0)
        self.period = int(round(PERIOD_S * sample_rate))
        self.burst = int(round(BURST_S * sample_rate))
        ramp = int(round(RAMP_S * sample_rate))
        env = np.ones(self.burst, dtype=np.float32)
        edge = (0.5 - 0.5 * np.cos(np.linspace(0, math.pi, ramp, dtype=np.float32))).astype(np.float32)
        env[:ramp] = edge
        env[-ramp:] = edge[::-1]
        t = np.arange(self.burst, dtype=np.float64) / sample_rate
        self._burst = (self.amp * env * np.sin(2 * math.pi * TONE_HZ * t)).astype(np.float32)

    def frame_samples(self, index: int) -> np.ndarray:
        """float32 mono samples covering video frame `index`."""
        n0 = index * self.samples_per_frame
        out = np.zeros(self.samples_per_frame, dtype=np.float32)
        phase = n0 % self.period
        # the burst can start inside this frame or have started in the previous one
        for start in (-self.period, 0, self.period):
            lo = max(0, start - phase)
            hi = min(self.samples_per_frame, start - phase + self.burst)
            if hi > lo and start - phase < self.samples_per_frame:
                src_lo = lo - (start - phase)
                out[lo:hi] = self._burst[src_lo : src_lo + (hi - lo)]
        return out

    def onset_time(self, k: int) -> float:
        """Ground-truth onset (seconds on the sender timeline) of the k-th burst."""
        return k * PERIOD_S


class BeepDetector:
    """Streaming onset detector: short-time RMS (5 ms hop) crossing -30 dBFS, 0.5 s refractory."""

    def __init__(self, sample_rate: int, hop_s: float = HOP_S, threshold_dbfs: float = ONSET_THRESHOLD_DBFS):
        self.sr = sample_rate
        self.hop = max(1, int(round(hop_s * sample_rate)))
        self.threshold = 10.0 ** (threshold_dbfs / 20.0)
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0  # absolute sample index of _buf[0]
        self._above = False
        self._last_onset = -1e9
        self._anchored = False
        self.onsets: list[float] = []  # seconds on the stream timeline

    def feed(self, samples: np.ndarray, pts_s: float) -> None:
        """Feed mono float samples that start at stream time `pts_s`."""
        start = int(round(pts_s * self.sr))
        if not self._anchored:
            self._buf_start = start
            self._anchored = True
        elif start != self._buf_start + len(self._buf):  # a gap or overlap: restart the hop grid
            self._buf = np.zeros(0, dtype=np.float32)
            self._buf_start = start
        self._buf = np.concatenate([self._buf, samples.astype(np.float32, copy=False)])
        n_hops = len(self._buf) // self.hop
        for h in range(n_hops):
            seg = self._buf[h * self.hop : (h + 1) * self.hop]
            rms = float(np.sqrt(np.mean(seg * seg)))
            above = rms >= self.threshold
            if above and not self._above:
                t0 = (self._buf_start + h * self.hop) / self.sr
                loud = np.flatnonzero(np.abs(seg) > 0.01)
                onset = t0 + (int(loud[0]) / self.sr if loud.size else 0.0)
                if onset - self._last_onset > 0.5:
                    self.onsets.append(onset)
                    self._last_onset = onset
            self._above = above
        consumed = n_hops * self.hop
        self._buf = self._buf[consumed:]
        self._buf_start += consumed


def detect_beeps(samples: np.ndarray, sample_rate: int, t0: float = 0.0) -> list[float]:
    """Offline helper: onset times (seconds) in a mono float array that starts at `t0`."""
    det = BeepDetector(sample_rate)
    det.feed(samples, t0)
    return det.onsets
