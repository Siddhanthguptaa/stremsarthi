"""The default motion path must reach back over the whole delay (G1 root cause: history of 120 frames).

At delay D the newest frame is ``fps * D`` frames ahead of the frame being decided. A history that holds
only 120 frames evicts it at D=4 s, every projection is unresolved and every frame becomes PROTECT, even
though the lanes covered it. These tests use the engine's default ``MotionWorker`` (real worker thread)
with arrival times shifted into the future so the real thread clock is always before every deadline.
"""

import logging
import math
import time

import numpy as np
import pytest

from contracts import Frame, Lane
from engine.control import LIVE
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion
from engine.testing import lane_result

FPS = 30.0


def textured(seq: int) -> np.ndarray:
    """A 160x90 textured frame sliding 1 px per frame (a stable, estimable global motion)."""
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (90, 320, 3), dtype=np.uint8)
    return np.ascontiguousarray(base[:, seq % 160 : seq % 160 + 160])


def wait_idle(engine: DeadlineEngine, expected: int) -> None:
    deadline = time.monotonic() + 10
    while engine.motion.completed < expected and time.monotonic() < deadline:  # type: ignore[attr-defined]
        time.sleep(0.001)


@pytest.mark.parametrize("delay", [3.0, 4.0, 5.0])
def test_default_motion_resolves_a_frame_at_its_deadline(delay):
    t0 = time.monotonic() + 1000.0  # every real completion precedes every virtual deadline
    engine = DeadlineEngine((Lane.FAST, Lane.ACCURATE), fps=FPS, delay_seconds=delay)
    states: dict[int, tuple[str, str]] = {}
    try:
        total = int(FPS * delay) + 60
        for seq in range(total):
            arrival = t0 + seq / FPS
            frame = Frame(seq=seq, pts=seq / FPS, arrival=arrival, image=textured(seq))
            assert engine.submit(frame, now=arrival)
            if seq % 3 == 0:
                engine.submit_result(lane_result(Lane.FAST, seq, arrival + 0.03))
            if seq % 10 == 0:
                engine.submit_result(lane_result(Lane.ACCURATE, seq, arrival + 0.4))
            wait_idle(engine, seq + 1)
            for decision in engine.poll(now=arrival):
                states[decision.seq] = (decision.state, decision.reason)
        decided = [s for s in sorted(states) if s >= 3]
        assert len(decided) > 40
        bad = {s: states[s] for s in decided if states[s][0] != LIVE}
        assert not bad, f"frames not LIVE at D={delay}: {dict(list(bad.items())[:5])}"
        assert not engine.motion.stats["unresolved"]  # type: ignore[attr-defined]
    finally:
        engine.close()


def test_history_size_covers_delay_plus_lookback():
    for delay in (3.0, 4.0, 5.0):
        engine = DeadlineEngine((Lane.FAST,), fps=FPS, delay_seconds=delay, window_before=6)
        try:
            assert engine.motion.history_capacity >= math.ceil(FPS * delay) + 6  # type: ignore[attr-defined]
        finally:
            engine.close()


def test_undersized_motion_history_is_logged(caplog):
    class Short(IdentityMotion):
        history_capacity = 120

    with caplog.at_level(logging.WARNING, logger="streamsarthi.engine.deadline"):
        DeadlineEngine((Lane.FAST,), fps=FPS, delay_seconds=4.0, motion=Short())
    assert any("motion history keeps 120 frames" in r.message for r in caplog.records)


def test_unresolved_reasons_are_counted():
    engine = DeadlineEngine((Lane.FAST,), fps=FPS, delay_seconds=1.0)
    try:
        assert engine.motion.project_many([], 0, 5, deadline=1.0) is None  # type: ignore[attr-defined]
        assert engine.motion.stats["unresolved"] == {"motion-not-computed": 1}  # type: ignore[attr-defined]
    finally:
        engine.close()
