"""guard.music:build with an in-process bus: audio in, RiskSignals out on Topic.RISK."""

from __future__ import annotations

import time

import numpy as np

from contracts.config import Config
from contracts.enums import Category, Topic
from contracts.events import RiskSignal
from contracts.fakes.bus import InProcessBus
from contracts.media import AudioChunk
from guard.music import build
from guard.music.detector import HOP, SR, WINDOW
from guard.music.service import MusicGuard
from guard.music.synth import synth_song


class _Fixed:
    backend = "fixed"

    def score(self, samples: np.ndarray) -> np.ndarray:
        n = len(samples)
        return np.full(0 if n < WINDOW else (n - WINDOW) // HOP + 1, 0.95, dtype=np.float32)


def _chunks(x: np.ndarray) -> list[AudioChunk]:
    pcm = (x * 32767).astype(np.int16)
    step = SR // 50 * 25  # 0.5 s
    return [AudioChunk(i, k / SR, SR, pcm[k : k + step], 0.0) for i, k in enumerate(range(0, len(pcm), step))]


def test_build_start_feed_publish_close() -> None:
    bus = InProcessBus()
    svc = build(Config(), bus)
    svc.guard = MusicGuard(Config().guard, scorer=_Fixed(), wall_clock=lambda: "2026-11-21T00:00:00+00:00")
    svc.start()
    for c in _chunks(synth_song(103, 14.0)):
        svc.feed(c)
    deadline = time.time() + 10
    while time.time() < deadline and not any(s.source == "music" for s in bus.of(Topic.RISK)):
        time.sleep(0.1)
    svc.close()
    sigs = bus.of(Topic.RISK)
    assert sigs and all(isinstance(s, RiskSignal) for s in sigs)
    assert any(s.category is Category.MUSIC and s.label == "music" for s in sigs)


def test_disabled_guard_is_inert_and_start_takes_no_arguments() -> None:
    cfg = Config()
    cfg.guard.enabled = False
    bus = InProcessBus()
    svc = build(cfg, bus)
    svc.start()
    for c in _chunks(synth_song(103, 3.0)):
        svc.feed(c)
    svc.close()
    assert bus.of(Topic.RISK) == []
