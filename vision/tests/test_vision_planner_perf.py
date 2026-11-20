"""Planner plus tracker cost per lane run (CPU, no models): under 2 ms with 20 tracks (T2.3 budget)."""

from __future__ import annotations

import time

from contracts import Category, Config, Detection, Lane, Quad
from vision.planner import MaskPlanner

CFG = Config(profile="dev", _env_file=None)
BUDGET_MS = 2.0
CATS = [Category.AADHAAR, Category.PHONE_NUMBER, Category.FACE, Category.EMAIL, Category.PIN_CODE]


def _scene(k: int, mixed: bool) -> tuple[float, list[Detection]]:
    pts = k / 10.0
    dets = []
    for j in range(20):
        if mixed:  # five categories, four boxes each, all moving
            cat = CATS[j % 5]
            x = 50 + (j % 5) * 240 + 30 * pts
            y = 40 + (j // 5) * 160 + 10 * pts
            box = (x, y, x + 100, y + 60)
        else:  # one signboard with four lines of five words each: exercises the line merge
            cat = Category.SIGNBOARD
            x = 50 + (j % 5) * 130
            y = 60 + (j // 5) * 70
            box = (x, y, x + 110, y + 30)
        dets.append(Detection("d", cat, Quad.from_xyxy(*box), 0.9, k * 3, pts, 0.0, extra={"reason": "x"}))
    return pts, dets


def _measure(mixed: bool, lane: Lane = Lane.FAST) -> list[float]:
    planner = MaskPlanner(lane, CFG)
    times: list[float] = []
    for k in range(260):
        pts, dets = _scene(k, mixed)
        t0 = time.perf_counter()
        planner.plan(pts, dets)
        times.append((time.perf_counter() - t0) * 1000.0)
    times = sorted(times[60:])  # steady state: tracks exist, caches warm
    return times


def test_planner_with_twenty_tracks_is_under_two_ms_per_run() -> None:
    for mixed in (True, False):
        times = _measure(mixed)
        p50, p95 = times[len(times) // 2], times[int(len(times) * 0.95)]
        print(
            f"plan() 20 tracks ({'mixed' if mixed else 'signboard lines'}): p50 {p50:.2f} ms, p95 {p95:.2f} ms"
        )
        assert p50 < BUDGET_MS
        assert p95 < 2 * BUDGET_MS  # a loaded CI box may spike; the median is the budget


def test_planner_cost_is_flat_over_time_no_track_leak() -> None:
    planner = MaskPlanner(Lane.FAST, CFG)
    for k in range(600):
        pts, dets = _scene(k, True)
        planner.plan(pts, dets if k % 50 < 40 else [])  # objects come and go
    assert planner.live_tracks <= 20  # lost tracks are dropped after the buffer
    assert len(planner.tracker.hysteresis) <= 20  # and so is the hysteresis state
