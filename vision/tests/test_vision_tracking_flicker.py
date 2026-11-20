"""T2.3 acceptance on synthetic trajectories: flicker, moving targets, occlusion, crossing, appear/disappear.

Uses the planner end to end (ByteTrack, hysteresis, projection, merge) and ``vision.tracking.sim`` for the
engine's mask selection. Two views of the painted mask are scored: ``engine`` (nearest result wins, results
after the frame are available through the delay buffer) and the stricter ``causal`` (only results up to the
frame). Boxes are 300x200 cards unless stated; detections carry 1.5 px of noise.
"""

from __future__ import annotations

import pytest

from contracts import Category, Config, Lane
from vision.planner import MaskPlanner
from vision.tracking import sim

CFG = Config(profile="dev", _env_file=None)
FPS = 30


def planner(lane: Lane = Lane.FAST, cfg: Config = CFG) -> MaskPlanner:
    return MaskPlanner(lane, cfg)


def test_flicker_counter_itself() -> None:
    on = [True] * 10 + [False] * 5 + [True] * 10 + [False] * 40 + [True] * 5 + [False] * 4
    assert sim.flicker_events(on, 30) == 1  # the 5-frame gap counts; the 40-frame gap (1.33 s) is too long
    assert sim.flicker_events([False] * 5 + [True] * 5, 30) == 0  # starting off is not a flicker
    assert sim.flicker_events([True] * 5 + [False] * 5, 30) == 0  # ending off is not a flicker


@pytest.mark.parametrize(
    ("cat", "detector", "conf", "lane"),
    [
        (Category.AADHAAR, "cards", 0.9, Lane.FAST),
        (Category.FACE, "faces", 0.8, Lane.FAST),
        (Category.AADHAAR, "cards", 0.9, Lane.ACCURATE),
        (Category.PHONE_NUMBER, "text", 0.6, Lane.ACCURATE),
    ],
)
@pytest.mark.parametrize("model", ["engine", "causal"])
def test_static_target_with_ten_percent_missed_detections_does_not_flicker(
    cat: Category, detector: str, conf: float, lane: Lane, model: str
) -> None:
    size = (120.0, 140.0) if cat is Category.FACE else (300.0, 200.0)
    target = sim.Target(sim.static(640, 360), size, cat, detector, conf)
    worst = 0.0
    for seed in (1, 2, 3):
        s = sim.run_lane(
            planner(lane), CFG, [target], lane=lane, duration_s=60, miss_prob=0.10, noise_px=1.5, seed=seed
        )
        assert any(not reqs or reqs[0].reason.endswith(":held") for _, _, reqs in s.runs)  # misses did happen
        on = sim.on_series(s, model)
        worst = max(worst, sim.flicker_events(on, FPS) / len(on))
    assert worst <= 0.02
    assert worst == 0.0  # hold covers every gap that 10 % misses can produce; any flicker is a regression


@pytest.mark.parametrize("miss", [0.0, 0.1, 0.3])
def test_flicker_with_heavy_misses_stays_under_the_limit(miss: float) -> None:
    target = sim.Target(sim.static(500, 300), (300, 200))
    s = sim.run_lane(planner(), CFG, [target], duration_s=60, miss_prob=miss, noise_px=1.5, seed=4)
    on = sim.on_series(s, "engine")
    assert sim.flicker_events(on, FPS) / len(on) <= 0.02


def test_unconfirmed_low_confidence_target_confirms_once_and_then_does_not_flicker() -> None:
    cfg = Config(profile="dev", _env_file=None)
    cfg.categories[Category.PIN_CODE] = cfg.categories[Category.PIN_CODE].model_copy(
        update={"mask_when_unsure": False}
    )
    target = sim.Target(sim.static(400, 300), (120, 30), Category.PIN_CODE, "text", 0.6)
    s = sim.run_lane(
        planner(Lane.ACCURATE, cfg), cfg, [target], lane=Lane.ACCURATE, duration_s=60, miss_prob=0.1, seed=2
    )
    on = sim.on_series(s, "engine")
    assert sum(on) / len(on) > 0.95
    assert sim.flicker_events(on, FPS) / len(on) <= 0.02


@pytest.mark.parametrize("velocity", [(200.0, 0.0), (0.0, 200.0), (141.0, 141.0), (-200.0, 80.0)])
@pytest.mark.parametrize("miss", [0.0, 0.1])
def test_card_moving_at_200_px_per_s_stays_covered_at_10_hz(
    velocity: tuple[float, float], miss: float
) -> None:
    vx, vy = velocity
    x0 = 150.0 if vx >= 0 else 1100.0
    y0 = 120.0
    # keep the whole 300x200 card inside the 1280x720 canvas for the whole run
    limits = []
    if vx > 0:
        limits.append((1280 - 150 - x0) / vx)
    if vx < 0:
        limits.append((x0 - 150) / -vx)
    if vy > 0:
        limits.append((720 - 100 - y0) / vy)
    duration = min(limits) - 0.05
    target = sim.Target(sim.linear(x0, y0, vx, vy), (300, 200))
    ratios = {"engine": [], "causal": []}
    # scored from the track's second sighting: with one sighting there is no velocity yet, and a miss right
    # after the first detection (about 1 % of starts at 10 % misses) lets a 200 px/s card outrun the mask
    for seed in (1, 2, 3, 4, 5):
        s = sim.run_lane(
            planner(),
            CFG,
            [target],
            duration_s=duration,
            miss_prob=miss,
            noise_px=1.5,
            seed=seed,
            detect_hz=10.0,
        )
        for model in ratios:
            ratios[model].append(sim.coverage_ratio(s, 0, model, threshold=0.98, sightings=2))
    assert min(ratios["engine"]) >= 0.98, ratios
    assert min(ratios["causal"]) >= 0.98, ratios


def test_moving_card_at_the_accurate_rate_is_mostly_covered() -> None:
    target = sim.Target(sim.linear(150, 120, 200, 0), (300, 200))
    s = sim.run_lane(
        planner(Lane.ACCURATE), CFG, [target], lane=Lane.ACCURATE, duration_s=4.6, noise_px=1.5, seed=5
    )
    assert sim.coverage_ratio(s, 0, "engine") >= 0.97  # 4 Hz: the first run has no velocity yet


def test_occlusion_of_ten_runs_never_unmasks_a_card() -> None:
    # detected for 2 s, hidden for 10 runs (1.0 s, still inside the 2.0 s card hold), visible again
    runs_hidden = (2.0, 3.0)

    class Gap(sim.Target):  # a Target that is absent for a window
        __slots__ = ()

        def present(self, pts: float) -> bool:
            return not (runs_hidden[0] <= pts < runs_hidden[1])

    target = Gap(sim.linear(200, 200, 100, 0), (300, 200))
    s = sim.run_lane(planner(), CFG, [target], duration_s=6, noise_px=1.0, seed=3)
    for model in ("engine", "causal"):
        # the object is physically there the whole time, so it must be covered the whole time
        assert sim.coverage_ratio(s, 0, model, threshold=0.95) >= 0.98
    ids = {r.track_id for _, _, reqs in s.runs for r in reqs}
    assert len(ids) == 1  # the same track before and after the occlusion


def test_vanished_card_is_unmasked_after_the_hold_and_not_before() -> None:
    target = sim.Target(sim.static(640, 360), (300, 200), t_to=2.0)
    s = sim.run_lane(planner(), CFG, [target], duration_s=8, seed=1)
    on = sim.on_series(s, "engine")
    first_off = next(i for i in range(int(2.2 * FPS), len(on)) if not on[i])
    assert first_off / FPS >= 2.0 + 2.0 - 0.1  # card hold 2.0 s after the last sighting
    assert first_off / FPS <= 2.0 + 2.0 + 0.5
    assert not any(on[first_off:])  # no ghost mask afterwards
    assert not planner().plan(0.0, [])[0]


def test_two_crossing_cards_keep_two_ids_and_stay_covered() -> None:
    a = sim.Target(sim.linear(200, 200, 200, 0), (300, 200))
    b = sim.Target(sim.linear(1080, 480, -200, -100), (180, 120), Category.PAN, "cards")
    s = sim.run_lane(planner(), CFG, [a, b], duration_s=4.0, noise_px=1.0, seed=2, detect_hz=10.0)
    assert sim.coverage_ratio(s, 0, "engine") >= 0.98
    assert sim.coverage_ratio(s, 1, "engine") >= 0.98
    ids = {(r.category, r.track_id) for _, _, reqs in s.runs for r in reqs}
    assert len(ids) == 2


def test_object_appearing_midway_is_masked_from_its_first_detection() -> None:
    target = sim.Target(sim.static(640, 360), (300, 200), t_from=2.0)
    s = sim.run_lane(planner(), CFG, [target], duration_s=5, seed=1)
    on = sim.on_series(s, "causal")
    first = next(i for i, v in enumerate(on) if v)
    assert first / FPS <= 2.0 + 0.1  # a card confirms on its first detection
    assert all(on[first:] if first + 20 < len(on) else [True])
