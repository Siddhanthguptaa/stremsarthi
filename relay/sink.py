"""``FrameSink`` implementations: the real encoder sink and a counting null sink for tests."""

from __future__ import annotations

import threading
from typing import Any

from relay.egress import Egress, EgressWriter


class EgressFrameSink:
    """Implements ``contracts.runtime.FrameSink``: encode once, fan out to every destination.

    ``write`` hands the frame to a single-slot writer thread and returns at once (about 1 ms for the
    private copy), so the engine tick never waits for encode, mux or a destination. ``healthy`` is false
    while the encoder is dead or backing off, or after the writer thread failed.
    """

    def __init__(self, egress: Egress, *, canvas: tuple[int, int] = (1280, 720)):
        self.egress = egress
        self.writer = EgressWriter(egress, shape=(canvas[1], canvas[0]))
        self._closed = False

    def write(self, pts: float, image: Any, audio_pcm48k: Any) -> None:
        if self._closed:
            return
        self.writer.write(float(pts), image, audio_pcm48k)

    @property
    def healthy(self) -> bool:
        return not self._closed and self.writer.alive and self.egress.healthy

    def stats(self) -> dict[str, Any]:
        return {
            "submitted": self.writer.submitted, "written": self.writer.written, "dropped": self.writer.dropped,
            "slow_writes": self.writer.slow_writes, "max_write_s": round(self.writer.max_write_s, 4),
            **self.egress.stats(),
        }  # fmt: skip

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.writer.close()


class NullFrameSink:
    """Implements ``contracts.runtime.FrameSink``: never blocks, keeps counters, writes nothing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.frames = 0
        self.audio_samples = 0
        self.last_pts: float | None = None
        self.pts_nonmonotonic = 0
        self.closed = False

    def write(self, pts: float, image: Any, audio_pcm48k: Any) -> None:
        with self._lock:
            if self.closed:
                return
            if self.last_pts is not None and pts <= self.last_pts:
                self.pts_nonmonotonic += 1
            self.last_pts = pts
            self.frames += 1
            self.audio_samples += int(len(audio_pcm48k)) if audio_pcm48k is not None else 0

    @property
    def healthy(self) -> bool:
        return not self.closed

    def close(self) -> None:
        with self._lock:
            self.closed = True
