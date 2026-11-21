"""FAST lane: a scene cut or a new target between two strides makes the next frame run at once (CPU only)."""

from __future__ import annotations

import time

import numpy as np

from contracts import Frame, Lane
from contracts.fakes import FakeDetector
from vision.lanes import LaneSchedule, LaneWorker, SceneChange


def _img(level: int = 90, patch: tuple[int, int, int, int] | None = None) -> np.ndarray:
    img = np.full((720, 1280, 3), level, np.uint8)
    if patch:
        x0, y0, x1, y1 = patch
        img[y0:y1, x0:x1] = 245
    return img


def test_scene_change_fires_on_a_cut_and_on_a_new_object_but_not_on_noise() -> None:
    sc = SceneChange()
    rng = np.random.default_rng(1)
    assert sc.update(_img()) is False  # first frame: nothing to compare with
    noisy = np.clip(_img().astype(np.int16) + rng.integers(-6, 7, (720, 1280, 3)), 0, 255).astype(np.uint8)
    assert sc.update(noisy) is False
    assert sc.update(_img(patch=(467, 145, 813, 578))) is True  # a card-sized object appears
    assert sc.update(_img(level=200)) is True  # hard cut
    assert sc.update(_img(level=200)) is False


def test_fast_lane_runs_off_stride_on_a_change() -> None:
    det = FakeDetector()
    w = LaneWorker(Lane.FAST, [det], LaneSchedule(stride=3))
    w.start()
    try:
        frames = [_img() for _ in range(22)] + [_img(patch=(467, 145, 813, 578)) for _ in range(4)]
        accepted = 0
        for seq, im in enumerate(frames):
            if w.offer(Frame(seq, seq / 30.0, time.monotonic(), im)):
                accepted += 1
                end = time.monotonic() + 3
                while w.completed < accepted and time.monotonic() < end:
                    time.sleep(0.002)
    finally:
        w.close()
    assert 22 in det.calls, "the first frame with the new object runs immediately"
    assert w.triggered == 1
    assert 24 in det.calls


def test_flashing_source_cannot_trigger_more_than_one_run_per_interval() -> None:
    from vision.lanes import TRIGGER_MIN_INTERVAL_S

    now = [100.0]
    det = FakeDetector()
    w = LaneWorker(Lane.FAST, [det], LaneSchedule(stride=3), clock=lambda: now[0])
    # not started: nothing consumes frames, so only the scheduling decision is exercised
    levels = [30, 220]
    taken = []
    for seq in range(1, 61):  # every frame flips brightness (seq 3, 6, ... are on stride and skipped here)
        if seq % 3 == 0:
            continue
        now[0] += 0.01  # 60 frames in 0.6 s
        w._latest = None
        taken.append(w.offer(Frame(seq, seq / 30.0, now[0], _img(levels[seq % 2]))))
    assert sum(taken) <= int(0.6 / TRIGGER_MIN_INTERVAL_S) + 1
    assert w.triggered == sum(taken) and w.trigger_suppressed >= 10
    assert w.stats["trigger_suppressed"] == w.trigger_suppressed


def test_a_single_new_object_still_triggers_at_once_when_no_trigger_ran_recently() -> None:
    now = [50.0]
    w = LaneWorker(Lane.FAST, [FakeDetector()], LaneSchedule(stride=3), clock=lambda: now[0])
    assert w.offer(Frame(1, 0.03, now[0], _img())) is False
    now[0] += 0.03
    assert w.offer(Frame(2, 0.06, now[0], _img(patch=(467, 145, 813, 578)))) is True
    assert w.triggered == 1 and w.trigger_suppressed == 0
