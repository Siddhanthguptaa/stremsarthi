"""MaskPlanner with ByteTrack: track ids, velocity projection, fallback, host wiring (CPU, no models)."""

from __future__ import annotations

import dataclasses
import time

import pytest

from contracts import Category, Config, Detection, Frame, Lane, Quad
from contracts.fakes import FakeDetector, InProcessBus
from vision import host as vhost
from vision.planner import (
    MAX_PROJECT_S,
    IouHoldTracker,
    MaskPlanner,
    TrackedDetection,
    planner_factory,
)
from vision.tracking import ByteTrackHold


def _cfg(**cats: dict) -> Config:
    cfg = Config(profile="dev", _env_file=None)
    for name, update in cats.items():
        cat = Category(name)
        cfg.categories[cat] = cfg.categories[cat].model_copy(update=update)
    return cfg


def _det(
    cat: Category = Category.AADHAAR,
    box: tuple[float, float, float, float] = (300, 200, 600, 400),
    conf: float = 0.9,
    pts: float = 0.0,
    quad: Quad | None = None,
    detector: str = "cards",
) -> Detection:
    return Detection(
        detector,
        cat,
        quad or Quad.from_xyxy(*box),
        conf,
        0,
        pts,
        0.0,
        extra={"reason": "verhoeff_ok"},
    )


def _moving(planner: MaskPlanner, vx: float, runs: int, hz: float = 10.0, x0: float = 100.0):
    """Feed ``runs`` detections of a 300x200 card moving at ``vx`` px/s; return the last (requests, pts)."""
    out = ((), ())
    pts = 0.0
    for k in range(runs):
        pts = k / hz
        x = x0 + vx * pts
        out = planner.plan(pts, [_det(box=(x, 200, x + 300, 400), pts=pts)])
    return out[0], pts


def test_default_planner_uses_bytetrack_at_the_lane_rate() -> None:
    cfg = _cfg()
    fast = MaskPlanner(Lane.FAST, cfg)
    acc = MaskPlanner(Lane.ACCURATE, cfg)
    assert isinstance(fast.tracker, ByteTrackHold) and isinstance(acc.tracker, ByteTrackHold)
    assert fast.rate_hz == pytest.approx(10.0) and acc.rate_hz == pytest.approx(4.0)
    assert fast.tracker.tracker.params.frame_rate == pytest.approx(10.0)
    assert acc.tracker.tracker.params.frame_rate == pytest.approx(4.0)
    assert isinstance(MaskPlanner(Lane.FAST, cfg, tracker="iou").tracker, IouHoldTracker)
    assert isinstance(planner_factory(Lane.FAST, cfg), MaskPlanner)


def test_requests_carry_stable_distinct_track_ids() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    ids_seen: list[dict[str, int | None]] = []
    for k in range(6):
        pts = k * 0.25
        a = _det(box=(100 + 10 * k, 100, 400 + 10 * k, 300), pts=pts)
        b = _det(box=(700, 400 - 8 * k, 1000, 600 - 8 * k), pts=pts)
        reqs, _ = planner.plan(pts, [a, b])
        assert len(reqs) == 2
        ids_seen.append({r.request_id: r.track_id for r in reqs})
    assert all(m == ids_seen[0] for m in ids_seen)
    assert len(set(ids_seen[0].values())) == 2 and None not in ids_seen[0].values()
    assert all(rid.endswith(f":{tid}") for rid, tid in ids_seen[0].items())


def test_two_categories_over_the_same_box_stay_two_masks() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    box = (300, 200, 600, 400)
    for k in range(3):
        reqs, _ = planner.plan(k * 0.25, [_det(Category.AADHAAR, box), _det(Category.PAN, box)])
        assert {r.category for r in reqs} == {Category.AADHAAR, Category.PAN}


def test_moving_card_mask_leads_the_detection_and_is_anchored_at_the_run() -> None:
    planner = MaskPlanner(Lane.FAST, _cfg())
    (req,), pts = _moving(planner, 200.0, 12)
    x = 100 + 200 * pts
    x1, _y1, x2, _y2 = req.quads[0].bbox()
    assert req.frame_pts == pytest.approx(pts) and req.pts_start == pytest.approx(pts)
    assert x1 < x - 12 and x2 > x + 300 + 12  # swept beyond the padded detection on both sides
    assert x2 - x1 < 300 + 2 * 12 + 2 * 200 * (planner.sweep_s + 0.01)  # and not by more than the sweep


def test_a_held_moving_mask_follows_its_velocity() -> None:
    planner = MaskPlanner(Lane.FAST, _cfg())
    _, pts = _moving(planner, 200.0, 12)
    last_x = 100 + 200 * pts
    for gap_s in (0.1, 0.3, 1.0):
        (req,), _ = planner.plan(pts + gap_s, [])
        assert req.reason.endswith(":held") and req.frame_pts == pytest.approx(pts + gap_s)
        x1, _y1, x2, _y2 = req.quads[0].bbox()
        # where the object would be now, with 200 px/s and a 12 px pad: covered
        assert x1 <= last_x + 200 * gap_s - 12 + 5 and x2 >= last_x + 300 + 200 * gap_s + 12 - 5
        # and the span back to where it was last seen is covered too (it may have stopped)
        assert x1 <= last_x and x2 >= last_x + 300


def test_projection_is_capped_at_two_seconds() -> None:
    planner = MaskPlanner(Lane.FAST, _cfg())
    det = _det(box=(100, 100, 400, 300))
    td = TrackedDetection(det, 0.0, True, 1, (100.0, 0.0))
    quad, anchor = planner.place(td, 10.0)  # 10 s of hold requested
    assert anchor == 10.0
    assert quad.bbox()[2] == pytest.approx(400 + 100 * (MAX_PROJECT_S + planner.sweep_s))
    assert MAX_PROJECT_S == 2.0


def test_static_tilted_card_keeps_its_exact_quad_and_anchor() -> None:
    tilted = Quad(((300.0, 220.0), (600.0, 200.0), (610.0, 400.0), (310.0, 420.0)))
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    last = None
    for k in range(5):
        (last,), _ = planner.plan(k * 0.25, [_det(quad=tilted, pts=k * 0.25)])
    ys = [p[1] for p in last.quads[0].pts]
    assert ys[0] != ys[1]  # still a tilted quad, not its axis-aligned hull
    (held,), _ = planner.plan(1.5, [])
    assert held.quads == last.quads  # not projected: no velocity
    assert held.frame_pts == pytest.approx(1.0)  # anchored at the last sighting (engine global motion hook)
    assert held.pts_start == pytest.approx(1.5)


def test_slow_drift_is_not_treated_as_motion() -> None:
    planner = MaskPlanner(Lane.FAST, _cfg())
    (req,), pts = _moving(planner, 8.0, 20)  # 8 px/s: above the noise floor, under the moving threshold
    assert req.frame_pts == pytest.approx(pts)  # fresh sighting anchors at its own PTS anyway
    (held,), _ = planner.plan(pts + 0.5, [])
    assert held.frame_pts == pytest.approx(pts)  # slow: anchored at the last sighting, not re-anchored


def test_bytetrack_failure_falls_back_to_the_iou_hold_for_good(caplog: pytest.LogCaptureFixture) -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    for k in range(3):
        planner.plan(k * 0.25, [_det(pts=k * 0.25)])

    def boom(*_a, **_k):
        raise RuntimeError("tracker bug")

    planner.tracker.update = boom  # type: ignore[method-assign]
    with caplog.at_level("ERROR", logger="vision.planner"):
        reqs, _ = planner.plan(0.75, [_det(pts=0.75)])
    assert planner.fell_back and isinstance(planner.tracker, IouHoldTracker)
    assert len(reqs) == 1  # the lane run still yields a mask (card: confirmed at once)
    assert "RuntimeError" in caplog.text and "tracker bug" not in caplog.text  # type only, never the message
    res = planner.lane_result(Frame(seq=9, pts=1.0, arrival=0.0, image=None), [])
    assert res.ok and len(res.requests) == 1  # still held by the fallback
    assert planner.plan(2.0, [_det(pts=2.0)])[0]  # keeps working afterwards


def test_a_supplied_tracker_is_not_replaced_when_it_fails() -> None:
    class Broken:
        def update(self, detections, pts):
            raise RuntimeError("no")

        def reset(self) -> None: ...

    planner = MaskPlanner(Lane.FAST, _cfg(), tracker=Broken())
    with pytest.raises(RuntimeError):
        planner.plan(0.0, [_det()])


def test_lane_result_heartbeat_while_a_track_is_held() -> None:
    planner = MaskPlanner(Lane.FAST, _cfg())
    planner.plan(0.0, [_det(Category.FACE, (100, 100, 200, 200), detector="faces")])
    held = planner.lane_result(Frame(seq=3, pts=0.1, arrival=0.0, image=None), [])
    assert held.ok and len(held.requests) == 1
    gone = planner.lane_result(Frame(seq=60, pts=2.0, arrival=0.0, image=None), [])
    assert gone.ok and gone.requests == ()  # the heartbeat stays ok with nothing to mask
    assert planner.live_tracks >= 0


def test_disabled_category_is_not_tracked_or_held() -> None:
    cfg = _cfg()
    planner = MaskPlanner(Lane.ACCURATE, cfg)
    planner.plan(0.0, [_det()])
    cfg.categories[Category.AADHAAR] = cfg.categories[Category.AADHAAR].model_copy(update={"enabled": False})
    assert planner.plan(0.25, []) == ((), ())  # config change while held: no more masks


def test_planner_time_is_tracker_driven_not_wall_clock() -> None:
    a = MaskPlanner(Lane.FAST, _cfg(), clock=lambda: 1.0)
    b = MaskPlanner(Lane.FAST, _cfg(), clock=lambda: 99.0)
    ra, _ = a.plan(0.0, [_det()])
    rb, _ = b.plan(0.0, [_det()])
    assert [dataclasses.replace(r, completed_at=0.0) for r in ra] == [
        dataclasses.replace(r, completed_at=0.0) for r in rb
    ]


class _MovingCard(FakeDetector):
    id = "mover"
    lane = Lane.FAST
    categories = frozenset({Category.AADHAAR})

    def detect(self, frame, ctx):
        x = 100 + 200 * frame.pts
        d = _det(box=(x, 200, x + 300, 400), pts=frame.pts)
        return [dataclasses.replace(d, frame_seq=frame.seq, completed_at=time.monotonic())]


def test_host_runs_a_bytetrack_planner_per_lane_and_reports_tracks() -> None:
    cfg = Config(profile="dev", _env_file=None)
    host = vhost.build(cfg, InProcessBus())
    assert set(host.assemblers) == host.active_lanes
    assert all(
        isinstance(p, MaskPlanner) and isinstance(p.tracker, ByteTrackHold) for p in host.assemblers.values()
    )
    host.close()
    both = vhost.VisionHost(cfg, InProcessBus(), registry={}, assembler_factory=planner_factory)
    assert both.assemblers[Lane.FAST] is not both.assemblers[Lane.ACCURATE]
    assert both.stats["fast"]["tracks"] == 0 and both.stats["accurate"]["tracker_fell_back"] is False
    both.close()
    host = vhost.VisionHost(
        cfg, InProcessBus(), registry={"mover": _MovingCard}, assembler_factory=planner_factory
    )
    host.start()
    try:
        results: list = []
        for seq in range(0, 40):
            host.submit(Frame(seq=seq, pts=seq / 30.0, arrival=time.monotonic(), image=None))
            time.sleep(0.003)
            results.extend(host.poll())
        time.sleep(0.3)
        results.extend(host.poll())
        stats = host.stats["fast"]
    finally:
        host.close()
    reqs = [r for res in results for r in res.requests]
    assert reqs and all(r.track_id == reqs[0].track_id for r in reqs)  # one object, one track id
    assert stats["tracks"] == 1 and stats["tracker_fell_back"] is False
    assert all(res.ok for res in results)


def _components(n: int, pairs) -> set[frozenset[int]]:
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        parent[find(a)] = find(b)
    groups: dict[int, set[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), set()).add(i)
    return {frozenset(g) for g in groups.values()}


def test_vectorised_line_links_match_group_lines() -> None:
    import random

    import numpy as np

    from vision.ocr.layout import group_lines
    from vision.planner import _line_links

    rng = random.Random(5)
    for trial in range(60):
        quads = []
        tilt = rng.choice([0.0, 0.0, 10.0, 25.0, 90.0])
        for _ in range(rng.randint(2, 14)):
            cx, cy = rng.uniform(100, 1100), rng.uniform(100, 600)
            w, h = rng.uniform(30, 200), rng.uniform(10, 40)
            a = np.radians(tilt + rng.uniform(-4, 4))
            r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
            pts = np.array([[-w, -h], [w, -h], [w, h], [-w, h]]) / 2.0
            quads.append(pts @ r.T + [cx, cy])
        # put some boxes on shared lines so there is something to merge
        for q in quads[: len(quads) // 2]:
            q[:, 1] -= q[:, 1].mean() - 300.0
        arr = np.array(quads, dtype=np.float64)
        want = _components(len(quads), [(line[0], m) for line in group_lines(list(arr)) for m in line[1:]])
        got = _components(len(quads), np.argwhere(_line_links(arr)).tolist())
        assert got == want, trial
