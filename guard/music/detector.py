"""Music presence detection: YAMNet (ONNX, CPU) with a smoothed on/off state machine (spec 5.1).

``MusicDetector.feed()`` takes 16 kHz mono float audio of any chunk size and returns one
``WindowResult`` per complete window (0.975 s window, 0.48 s hop). Music under speech is the normal
case, so speech never suppresses the music score. If the YAMNet weights are missing or fail their
SHA-256 check, the heuristic scorer in ``spectral.py`` is used and ``backend`` says so.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LOG = logging.getLogger("guard.music.detector")
SR = 16_000
WINDOW = 15_600
HOP = 7_680
YAMNET_SHA256 = "844ee869ca5426d12c6aa1876280c40099487bda74827da1ccbeeaffbe7143dd"
CLASS_MAP = Path(__file__).resolve().parent / "data" / "yamnet_class_map.csv"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = REPO_ROOT / "models" / "weights" / "yamnet.onnx"


def _class_index(name: str, path: Path = CLASS_MAP) -> int:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["display_name"] == name:
                return int(row["index"])
    raise KeyError(name)


def resolve_model_path(path: str | os.PathLike[str] | None = None) -> Path:
    return Path(path or os.environ.get("SS_YAMNET_ONNX") or DEFAULT_MODEL)


class YamnetScorer:
    """P(Music) and P(Speech) per complete window, from the YAMNet ONNX graph."""

    backend = "yamnet"

    def __init__(self, model_path: str | os.PathLike[str] | None = None, threads: int = 1) -> None:
        import onnxruntime as ort

        path = resolve_model_path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"YAMNet weights not found: {path}")
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if h != YAMNET_SHA256:
            raise ValueError(f"YAMNet SHA-256 mismatch for {path}")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self.music_idx = _class_index("Music")  # resolved by name, never hard-coded
        self.speech_idx = _class_index("Speech")
        self.last_speech: np.ndarray = np.zeros(0, dtype=np.float32)

    def score(self, samples: np.ndarray) -> np.ndarray:
        n = len(samples)
        if n < WINDOW:
            return np.zeros(0, dtype=np.float32)
        k = (n - WINDOW) // HOP + 1
        x = np.ascontiguousarray(
            samples[: WINDOW + HOP * (k - 1)], dtype=np.float32
        )  # exact fit: no padded frame
        scores = self._session.run([self._session.get_outputs()[0].name], {self._input: x})[0]
        self.last_speech = scores[:, self.speech_idx].astype(np.float32)  # diagnostics only
        return scores[:, self.music_idx].astype(np.float32)


def make_scorer(model_path: str | os.PathLike[str] | None = None) -> YamnetScorer | object:
    try:
        return YamnetScorer(model_path)
    except Exception as exc:  # missing, corrupt or unloadable weights
        LOG.warning("YAMNet unavailable (%s); using the spectral fallback music detector", type(exc).__name__)
        from .spectral import SpectralScorer

        return SpectralScorer()


@dataclass(frozen=True)
class WindowResult:
    t_end: float  # stream seconds at the end of the window
    p_music: float
    smoothed: float
    on: bool


class MusicStateMachine:
    """EMA smoothing (alpha 0.6, not the spec 0.4: 0.4 gave a 2.25 s median ON delay, 0.6 gives 1.8 s) plus hysteresis: ON after N windows at or above the on-threshold, OFF after N below off."""

    def __init__(
        self,
        on_threshold: float = 0.5,
        off_threshold: float = 0.3,
        on_windows: int = 3,
        off_windows: int = 3,
        alpha: float = 0.6,
    ) -> None:
        self.on_threshold, self.off_threshold = on_threshold, off_threshold
        self.on_windows, self.off_windows, self.alpha = on_windows, off_windows, alpha
        self.on = False
        self.smoothed: float | None = None
        self._hi = 0
        self._lo = 0

    def update(self, p: float) -> tuple[float, bool]:
        # The EMA starts at the first value so the first music window is not held down by a zero start.
        self.smoothed = p if self.smoothed is None else self.alpha * p + (1.0 - self.alpha) * self.smoothed
        s = self.smoothed
        self._hi = self._hi + 1 if s >= self.on_threshold else 0
        self._lo = self._lo + 1 if s < self.off_threshold else 0
        if not self.on and self._hi >= self.on_windows:
            self.on = True
        elif self.on and self._lo >= self.off_windows:
            self.on = False
        return s, self.on


class MusicDetector:
    """Streaming wrapper: buffers audio, scores every complete window, drives the state machine."""

    def __init__(
        self, scorer: object | None = None, state: MusicStateMachine | None = None, start_pts: float = 0.0
    ) -> None:
        self.scorer = scorer if scorer is not None else make_scorer()
        self.state = state or MusicStateMachine()
        self._buf = np.zeros(0, dtype=np.float32)
        self._consumed = 0  # samples dropped from the front of the buffer
        self._start = start_pts

    @property
    def backend(self) -> str:
        return str(getattr(self.scorer, "backend", "unknown"))

    def reset_buffer(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)

    def feed(self, samples: np.ndarray) -> list[WindowResult]:
        self._buf = np.concatenate([self._buf, np.asarray(samples, dtype=np.float32)])
        out: list[WindowResult] = []
        if len(self._buf) < WINDOW:
            return out
        p = self.scorer.score(self._buf)  # type: ignore[attr-defined]
        k = len(p)
        for i, v in enumerate(p):
            s, on = self.state.update(float(v))
            t_end = self._start + (self._consumed + i * HOP + WINDOW) / SR
            out.append(WindowResult(t_end, float(v), s, on))
        drop = k * HOP  # keep the overlap needed by the next window
        self._buf = self._buf[drop:]
        self._consumed += drop
        return out
