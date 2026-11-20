"""Hot-path media types. These carry numpy arrays and are NEVER put on the wire/bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .geometry import CANVAS_H, CANVAS_W


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded video frame on the canonical canvas.

    ``image`` is an ``(720, 1280, 3)`` uint8 BGR ``numpy.ndarray`` (typed ``Any`` so that the
    contracts package imports without numpy). It is read-only: a detector must not mutate it
    and must not retain it after ``detect()`` returns.
    """

    seq: int  # monotonic per session
    pts: float  # source time in seconds, rebased to 0 at session start
    arrival: float  # time.monotonic() when decoded
    image: Any

    def validate_canvas(self) -> None:
        shape = getattr(self.image, "shape", None)
        if shape is None or tuple(shape) != (CANVAS_H, CANVAS_W, 3):
            raise ValueError(f"Frame.image must have shape ({CANVAS_H}, {CANVAS_W}, 3), got {shape}")


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """Analysis-tap audio: 16 kHz mono int16 (``numpy.ndarray``), a multiple of 20 ms."""

    seq: int
    pts_start: float
    sample_rate: int
    pcm: Any
    arrival: float

    @property
    def n_samples(self) -> int:
        return int(len(self.pcm))

    @property
    def duration(self) -> float:
        return self.n_samples / float(self.sample_rate)

    @property
    def pts_end(self) -> float:
        return self.pts_start + self.duration
