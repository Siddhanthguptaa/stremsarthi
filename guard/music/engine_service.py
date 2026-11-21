"""Engine-loadable wrapper: ``guard.music:build(config, bus)`` (register as ``runtime.extras.guard_music``).

Lifecycle for ``engine/main.py``: ``start()`` takes no arguments and runs the music worker on a daemon
thread; ``close()`` stops it. Every ``RiskSignal`` is published on ``Topic.RISK`` (the risk aggregator
in ``guard/risk`` subscribes to that topic).

There is no bus topic for audio, so the analysis-tap audio reaches this service by a direct call:
the audio host (or the engine's audio tee) must call ``feed(chunk)`` with each ``AudioChunk``
(16 kHz mono int16). ``feed`` only queues the samples and returns at once.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from contracts.config import Config
from contracts.enums import Topic
from contracts.events import RiskSignal

from .service import MusicGuard

LOG = logging.getLogger("guard.music.engine")


class MusicService:
    def __init__(self, config: Config, bus: Any, guard: MusicGuard | None = None) -> None:
        self.bus = bus
        self.enabled = bool(config.guard.enabled)
        self.guard = guard if guard is not None else MusicGuard(config.guard)
        self._started = False
        self.published = 0
        self.feed_errors = 0

    def feed(self, chunk: Any) -> None:
        """Queue one ``AudioChunk`` (int16 PCM at 16 kHz). Never blocks, never raises into the audio path."""
        if not self.enabled:
            return
        try:
            pcm = np.asarray(chunk.pcm)
            x = pcm.astype(np.float32) / 32768.0 if pcm.dtype.kind == "i" else pcm.astype(np.float32)
            self.guard.push(x)
        except Exception as exc:  # the audio path must never see a guard failure
            self.feed_errors += 1
            if self.feed_errors == 1:  # ~50 chunks/s: log the first failure only, then count
                LOG.warning(
                    "music feed failed: %s (further failures are counted, see stats)", type(exc).__name__
                )

    def _publish(self, signal: RiskSignal) -> None:
        self.bus.publish(Topic.RISK, signal)
        self.published += 1

    def start(self) -> None:
        if self._started or not self.enabled:
            return
        self._started = True
        self.guard.start(self._publish)

    @property
    def stats(self) -> dict[str, object]:
        return {"published": self.published, "feed_errors": self.feed_errors, **self.guard.stats}

    def close(self) -> None:
        self.guard.stop()
        self._started = False

    stop = close


def build(config: Config, bus: Any) -> MusicService:
    return MusicService(config, bus)
