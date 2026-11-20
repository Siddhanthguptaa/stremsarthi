"""MaskPlanner and the IoU-hold tracker (CPU, no models)."""

from __future__ import annotations

import time

import pytest

from contracts import (
    Category,
    Config,
    Detection,
    Frame,
    Lane,
    MaskKind,
    MaskStyle,
    Quad,
)
from vision.lanes import LaneSchedule, LaneWorker
from vision.planner import MaskPlanner, min_confidence


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
    *,
    allow: bool = False,
    aux: dict | None = None,
    reason: str = "verhoeff_ok",
    detector: str = "cards",
) -> Detection:
    return Detection(
        detector,
        cat,
        Quad.from_xyxy(*box),
        conf,
        0,
        pts,
        0.0,
        allow=allow,
        aux_quads=aux or {},
        extra={"reason": reason},
    )


def _frame(seq: int = 0, pts: float = 0.0) -> Frame:
    return Frame(seq=seq, pts=pts, arrival=0.0, image=None)


def test_disabled_category_yields_nothing() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg(aadhaar={"enabled": False}))
    reqs, aux = planner.plan(0.0, [_det()])
    assert reqs == () and aux == ()


def test_allowed_face_gives_no_request_but_goes_to_aux() -> None:
    mouth = Quad.from_xyxy(10, 10, 20, 20)
    face = _det(Category.FACE, (0, 0, 100, 100), allow=True, aux={"mouth": mouth}, detector="faces")
    stranger = _det(Category.FACE, (300, 0, 400, 100), detector="faces")
    reqs, aux = MaskPlanner(Lane.FAST, _cfg()).plan(0.0, [face, stranger])
    assert [r.category for r in reqs] == [Category.FACE] and len(reqs) == 1
    assert aux == (face,)


def test_risky_text_masks_only_when_its_category_is_enabled() -> None:
    risky = _det(Category.RISKY_TEXT, (10, 10, 200, 60), detector="text")
    off = MaskPlanner(Lane.ACCURATE, _cfg(risky_text={"enabled": False}))
    assert off.plan(0.0, [risky]) == ((), ())
    on = MaskPlanner(Lane.ACCURATE, _cfg(risky_text={"enabled": True}))
    assert len(on.plan(0.0, [risky])[0]) == 1


def test_padding_style_clamp_and_request_fields() -> None:
    cfg = _cfg(aadhaar={"padding_px": 20, "style": MaskStyle.PIXELATE})
    planner = MaskPlanner(Lane.ACCURATE, cfg)
    (req,), _ = planner.plan(5.0, [_det(box=(300, 200, 600, 400), pts=5.0)])
    assert req.kind is MaskKind.REGION and req.style is MaskStyle.PIXELATE
    assert req.quads[0].bbox() == (280.0, 180.0, 620.0, 420.0)
    assert req.frame_pts == 5.0 == req.pts_start
    assert req.pts_end == pytest.approx(5.0 + 12 / 30)  # ACCURATE: lane_max_age 12 frames at 30 fps
    assert req.source == "cards" and req.category is Category.AADHAAR and req.reason == "verhoeff_ok"
    (edge,), _ = MaskPlanner(Lane.ACCURATE, cfg).plan(6.0, [_det(box=(5, 5, 100, 80), pts=6.0)])
    assert edge.quads[0].bbox()[:2] == (0.0, 0.0)
    fast = MaskPlanner(Lane.FAST, _cfg())
    (fr,), _ = fast.plan(0.0, [_det(Category.FACE, (100, 100, 200, 200), detector="faces")])
    assert fr.pts_end == pytest.approx(6 / 30)
    assert fr.quads[0].bbox() == pytest.approx((90.0, 90.0, 210.0, 210.0))  # faces: 10 % per side


def test_sensitivity_floor_and_mask_when_unsure() -> None:
    cfg = _cfg(pin_code={"mask_when_unsure": False})
    assert min_confidence(cfg.categories[Category.PIN_CODE], Category.PIN_CODE) == pytest.approx(0.5)
    assert min_confidence(cfg.categories[Category.AADHAAR], Category.AADHAAR) == pytest.approx(0.35)
    assert (
        min_confidence(
            cfg.categories[Category.PIN_CODE].model_copy(update={"sensitivity": 0.0}), Category.PIN_CODE
        )
        == 0.85
    )
    assert (
        min_confidence(
            cfg.categories[Category.PIN_CODE].model_copy(update={"sensitivity": 1.0}), Category.PIN_CODE
        )
        == 0.15
    )
    planner = MaskPlanner(Lane.ACCURATE, cfg)
    assert planner.plan(0.0, [_det(Category.PIN_CODE, conf=0.4, detector="text")])[0] == ()
    assert len(planner.plan(1.0, [_det(Category.AADHAAR, conf=0.4)])[0]) == 1  # unsure floor 0.35


def test_overlapping_quads_merge_and_distinct_ones_stay_apart() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    a = _det(box=(300, 200, 600, 400), conf=0.8)
    b = _det(box=(320, 210, 620, 410), conf=0.95)
    far = _det(box=(900, 500, 1100, 650))
    reqs, _ = planner.plan(0.0, [a, b, far])
    assert len(reqs) == 2
    big = max(reqs, key=lambda r: r.quads[0].area_bbox())
    assert big.confidence == 0.95
    x1, y1, x2, y2 = big.quads[0].bbox()
    assert x1 <= 288 and y1 <= 188 and x2 >= 632 and y2 >= 422  # the union of both, padded


def test_text_boxes_on_one_line_merge() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg(phone_number={"padding_px": 0}))
    one = _det(Category.PHONE_NUMBER, (100, 100, 200, 130), detector="text")
    two = _det(Category.PHONE_NUMBER, (215, 102, 330, 132), detector="text")
    other_line = _det(Category.PHONE_NUMBER, (100, 300, 200, 330), detector="text")
    reqs, _ = planner.plan(0.0, [one, two, other_line])
    assert len(reqs) == 2
    merged = min(reqs, key=lambda r: r.quads[0].bbox()[1])
    assert merged.quads[0].bbox() == (100.0, 100.0, 330.0, 132.0)


def test_mask_outlives_a_missed_detection_for_the_hold_time() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    (first,), _ = planner.plan(10.0, [_det(pts=10.0)])
    assert "held" not in first.reason
    (held,), _ = planner.plan(10.25, [])  # next ACCURATE run found nothing
    assert held.reason == "verhoeff_ok:held"
    assert held.frame_pts == 10.0 and held.pts_start == 10.25  # anchored at the last sighting
    assert held.request_id == first.request_id and held.track_id == first.track_id
    assert held.quads == first.quads
    (still,), _ = planner.plan(11.9, [])  # cards hold 2.0 s
    assert still.reason.endswith(":held")
    assert planner.plan(12.2, []) == ((), ())


def test_hold_times_differ_by_category() -> None:
    planner = MaskPlanner(Lane.FAST, _cfg())
    planner.plan(0.0, [_det(Category.FACE, (0, 0, 50, 50), detector="faces")])
    assert len(planner.plan(0.7, [])[0]) == 1
    assert planner.plan(0.9, [])[0] == ()  # faces hold 0.8 s
    text = MaskPlanner(Lane.ACCURATE, _cfg())
    text.plan(0.0, [_det(Category.EMAIL, (0, 0, 80, 20), detector="text")])
    assert len(text.plan(0.9, [])[0]) == 1 and text.plan(1.1, [])[0] == ()  # text 1.0 s


def test_two_hits_confirm_a_weak_track_but_strong_or_unsure_confirm_at_once() -> None:
    cfg = _cfg(pin_code={"mask_when_unsure": False, "sensitivity": 1.0})
    planner = MaskPlanner(Lane.ACCURATE, cfg)
    weak = _det(Category.PIN_CODE, (10, 10, 90, 40), conf=0.6, detector="text")
    assert planner.plan(0.0, [weak])[0] == ()  # first hit: not confirmed
    assert len(planner.plan(0.25, [weak])[0]) == 1  # second hit confirms
    strong = MaskPlanner(Lane.ACCURATE, cfg)
    assert (
        len(strong.plan(0.0, [_det(Category.PIN_CODE, (10, 10, 90, 40), conf=0.85, detector="text")])[0]) == 1
    )
    unsure = MaskPlanner(Lane.ACCURATE, _cfg())
    assert len(unsure.plan(0.0, [_det(conf=0.6)])[0]) == 1


def test_request_ids_are_stable_per_track_and_distinct_per_object() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    card1 = _det(box=(100, 100, 400, 300))
    card2 = _det(box=(700, 400, 1000, 600))
    r1, _ = planner.plan(0.0, [card1, card2])
    moved = _det(box=(110, 104, 410, 304))
    r2, _ = planner.plan(0.25, [moved, card2])
    assert {r.request_id for r in r1} == {r.request_id for r in r2} and len(r1) == 2


def test_source_clock_restart_drops_old_tracks() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    planner.plan(100.0, [_det(pts=100.0)])
    assert planner.plan(0.5, [])[0] == ()  # pts jumped back: the held mask must not leak into the new session


def test_empty_run_is_an_ok_heartbeat_lane_result() -> None:
    planner = MaskPlanner(Lane.ACCURATE, _cfg())
    res = planner.lane_result(_frame(7, 0.25), [])
    assert res.ok and res.requests == () and res.frame_seq == 7 and res.lane is Lane.ACCURATE
    res2 = planner.lane_result(_frame(8, 0.5), [_det()])
    assert res2.ok and len(res2.requests) == 1


def test_planner_is_a_lane_assembler() -> None:
    cfg = _cfg()

    class OneCard:
        id = "cards"
        lane = Lane.ACCURATE
        categories = frozenset({Category.AADHAAR})
        device = None
        vram_mb = 0

        def configure(self, cfg, res) -> None: ...
        def warmup(self) -> None: ...
        def close(self) -> None: ...

        def detect(self, frame, ctx):
            return [_det(pts=frame.pts)]

    worker = LaneWorker(
        Lane.ACCURATE,
        [OneCard()],  # type: ignore[list-item]
        LaneSchedule(rate_hz=50.0),
        categories=cfg.categories,
        assembler=MaskPlanner(Lane.ACCURATE, cfg),
    )
    worker.start()
    try:
        worker.offer(_frame(0, 0.0))
        deadline = time.monotonic() + 2.0
        out: list = []
        while not out and time.monotonic() < deadline:
            time.sleep(0.01)
            out = worker.drain()
    finally:
        worker.close()
    assert out and out[0].ok and out[0].requests[0].category is Category.AADHAAR
