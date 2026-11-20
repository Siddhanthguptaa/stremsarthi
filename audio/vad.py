"""Streaming voice activity detection: Silero VAD on ONNX Runtime (CPU, no torch).

The Silero model (MIT, snakers4/silero-vad) is a small recurrent net. It takes 512-sample windows
(32 ms at 16 kHz) plus a 64-sample context from the previous window and returns a speech
probability. ``StreamingVad`` accepts audio in any 20 ms multiple (the engine tap), buffers it into
512-sample windows, and applies on/off hysteresis from ``Config.audio.vad``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from contracts.config import VadConfig

SAMPLE_RATE = 16_000
WINDOW = 512  # samples per Silero window at 16 kHz (32 ms)
CONTEXT = 64  # samples of left context the v5/v6 model expects in front of each window


class FrameModel(Protocol):
    """Anything that maps one 512-sample float32 window to a speech probability, with state."""

    def reset(self) -> None: ...
    def __call__(self, window: Any) -> float: ...


class SileroOnnx:
    """Silero VAD v5/v6 ONNX model. ONNX Runtime CPU provider only, one thread."""

    def __init__(
        self, model_path: Path | str | None = None, *, threads: int = 1, download: bool = True
    ) -> None:
        import onnxruntime as ort

        from audio.models import ensure_silero_vad

        path = Path(model_path) if model_path else ensure_silero_vad(download=download)
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = threads
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT), dtype=np.float32)

    def __call__(self, window: Any) -> float:
        x = np.concatenate([self._context, np.asarray(window, dtype=np.float32).reshape(1, WINDOW)], axis=1)
        out, state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._state = state
        self._context = x[:, -CONTEXT:]
        return float(out[0, 0])


class EnergyFrameModel:
    """Level-based stand-in for Silero: probability rises smoothly around ``center_dbfs``.

    Used by unit tests (tones and silence) and as an emergency detector when the Silero model
    cannot be loaded. It also fires on any loud noise, which errs toward muting, never toward leaking.
    """

    def __init__(self, center_dbfs: float = -40.0, slope_db: float = 3.0) -> None:
        self.center_dbfs = center_dbfs
        self.slope_db = slope_db

    def reset(self) -> None:
        return None

    def __call__(self, window: Any) -> float:
        w = np.asarray(window, dtype=np.float32)
        rms = float(np.sqrt(np.mean(w * w))) if w.size else 0.0
        dbfs = 20.0 * math.log10(max(rms, 1e-5))
        return 1.0 / (1.0 + math.exp(-(dbfs - self.center_dbfs) / self.slope_db))


@dataclass(frozen=True, slots=True)
class VadFrame:
    start: int  # absolute sample index of the window's first sample
    prob: float
    speech: bool  # after hysteresis


class Hysteresis:
    """Speech turns on at ``prob >= on`` and off at ``prob < off`` (off < on)."""

    def __init__(self, on: float, off: float) -> None:
        if off > on:
            raise ValueError("off threshold must not exceed the on threshold")
        self.on = on
        self.off = off
        self.active = False

    def reset(self) -> None:
        self.active = False

    def step(self, prob: float) -> bool:
        if self.active:
            if prob < self.off:
                self.active = False
        elif prob >= self.on:
            self.active = True
        return self.active


class StreamingVad:
    """Feeds PCM of any length (int16 or float32) through a ``FrameModel`` in 512-sample windows."""

    def __init__(self, model: FrameModel, cfg: VadConfig | None = None) -> None:
        cfg = cfg or VadConfig()
        self.model = model
        self.cfg = cfg
        self._gate = Hysteresis(cfg.on_threshold, cfg.off_threshold)
        self._pending = np.zeros(0, dtype=np.float32)
        self._consumed = 0  # samples already turned into windows

    @classmethod
    def silero(cls, cfg: VadConfig | None = None, model_path: Path | str | None = None) -> StreamingVad:
        return cls(SileroOnnx(model_path), cfg)

    @property
    def window(self) -> int:
        return WINDOW

    @property
    def position(self) -> int:
        """Absolute index of the first sample not yet part of a window."""
        return self._consumed

    def reset(self) -> None:
        self.model.reset()
        self._gate.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        self._consumed = 0

    def restart(self, position: int) -> None:
        """Forget model state and buffered audio but keep absolute indices (after a gap in the stream)."""
        self.reset()
        self._consumed = position

    def feed(self, pcm: Any) -> list[VadFrame]:
        data = np.asarray(pcm)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        else:
            data = data.astype(np.float32, copy=False)
        buf = np.concatenate([self._pending, data]) if self._pending.size else data
        n_windows = len(buf) // WINDOW
        frames: list[VadFrame] = []
        for i in range(n_windows):
            prob = self.model(buf[i * WINDOW : (i + 1) * WINDOW])
            frames.append(VadFrame(self._consumed, prob, self._gate.step(prob)))
            self._consumed += WINDOW
        self._pending = buf[n_windows * WINDOW :].copy()
        return frames

    def flush(self) -> list[VadFrame]:
        """Run the trailing partial window (zero padded). Call once at end of stream."""
        if not self._pending.size:
            return []
        pad = np.zeros(WINDOW - len(self._pending), dtype=np.float32)
        window = np.concatenate([self._pending, pad])
        prob = self.model(window)
        frame = VadFrame(self._consumed, prob, self._gate.step(prob))
        self._consumed += len(self._pending)
        self._pending = np.zeros(0, dtype=np.float32)
        return [frame]
