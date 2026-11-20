"""Cards through ``VisionHost`` -> ``MaskPlanner`` -> ``LaneResult`` (fake OCR, CPU, no models), plus the
slow acceptance run with the real OCR (skips without weights)."""

from __future__ import annotations

import time

import pytest

from contracts import Category, Config, Frame, Lane, LaneHost, MaskKind, OcrResult
from contracts.fakes import InProcessBus
from models.prepare import DEFAULT_CACHE, artifact_path
from vision import host as vhost
from vision.accept_cards import run as run_acceptance
from vision.detectors.cards import DETECTOR
from vision.planner import planner_factory
from vision.resources import OcrUnavailableError, VisionResources
from vision.testing import Scene, augmented_set, make_card_scene, make_negative_scene, scene_ocr

WEIGHTS = all(artifact_path(n, DEFAULT_CACHE).is_file() for n in ("ppocr-v6-tiny", "ppocr-v5-english-rec"))


def _cfg() -> Config:
    return Config(profile="dev", _env_file=None)


def _routing_ocr(scenes: dict[int, Scene]):
    """Fake OCR that answers from the scene of the frame being processed (by seq)."""
    makers = {seq: scene_ocr(sc) for seq, sc in scenes.items()}

    def ocr(frame: Frame) -> OcrResult:
        maker = makers.get(frame.seq)
        return maker(frame) if maker else OcrResult((), (), (), ())

    return ocr


def _drive(host: vhost.VisionHost, scenes: dict[int, Scene], pts: dict[int, float], wait: float = 5.0):
    out = {}
    for seq, sc in scenes.items():
        host.submit(Frame(seq, pts[seq], 0.0, sc.image))
        end = time.monotonic() + wait
        while seq not in out and time.monotonic() < end:
            for r in host.poll():
                if r.lane is Lane.ACCURATE:
                    out[r.frame_seq] = r
            time.sleep(0.005)
        assert seq in out, f"no result for frame {seq}"
    return out


def test_build_wires_cards_and_the_planner_and_satisfies_lanehost() -> None:
    host = vhost.build(_cfg(), InProcessBus())
    try:
        assert isinstance(host, LaneHost)
        assert set(host.classes) >= {"cards"} and Lane.ACCURATE in host.active_lanes
        assert host.declared_vram_mb == 0  # CPU provider: nothing declared against the GPU cap
    finally:
        host.close()


def test_specimen_card_through_the_host_yields_a_whole_card_mask_then_heartbeats() -> None:
    card = make_card_scene("aadhaar", 1, 41, tilt=8.0, width_frac=0.4)
    empty = make_negative_scene("desk", 0, 41)
    cfg = _cfg()
    res = VisionResources(cfg, ocr=_routing_ocr({0: card}))
    host = vhost.VisionHost(
        cfg, InProcessBus(), registry={"cards": DETECTOR}, resources=res, assembler_factory=planner_factory
    )
    host.start()
    try:
        out = _drive(host, {0: card, 1: empty, 2: empty}, {0: 0.0, 1: 0.3, 2: 10.0})
    finally:
        host.close()
    first = out[0]
    assert first.ok and len(first.requests) == 1
    req = first.requests[0]
    assert req.category is Category.AADHAAR and req.kind is MaskKind.REGION and req.source == "cards"
    assert req.reason == "verhoeff_ok" and req.pts_end == pytest.approx(0.0 + 12 / 30)
    # card not seen on the next run (0.3 s later): the mask is held, anchored at the sighting
    held = out[1]
    assert held.ok and len(held.requests) == 1 and held.requests[0].reason.endswith(":held")
    assert held.requests[0].frame_pts == 0.0 and held.requests[0].quads == req.quads
    # 10 s later the hold is long over: an ok heartbeat with no request
    assert out[2].ok and out[2].requests == ()


def test_two_cards_in_one_frame_give_two_requests() -> None:
    a = make_card_scene("aadhaar", 2, 42, tilt=3.0, width_frac=0.3, center_x=(0.22, 0.28), clutter=False)
    b = make_card_scene("pan", 2, 42, tilt=-3.0, width_frac=0.3, center_x=(0.72, 0.78), clutter=False)
    # compose: left half of the frame from a, right half from b (cards sit in their own halves)
    import numpy as np

    img = a.image.copy()
    img[:, 640:] = b.image[:, 640:]
    both = Scene(img, "aadhaar")

    def ocr(frame: Frame) -> OcrResult:
        parts = [scene_ocr(a, with_text=False)(frame), scene_ocr(b, with_text=False)(frame)]
        keep = []
        for p, left in zip(parts, (True, False), strict=True):
            for q, t, c, s in zip(p.boxes, p.texts, p.confs, p.scripts, strict=True):
                cx = np.mean([pt[0] for pt in q.pts])
                if (cx < 640) == left:
                    keep.append((q, t, c, s))
        return OcrResult(*(tuple(x) for x in zip(*keep, strict=True)))  # type: ignore[arg-type]

    cfg = _cfg()
    host = vhost.VisionHost(
        cfg,
        InProcessBus(),
        registry={"cards": DETECTOR},
        resources=VisionResources(cfg, ocr=ocr),
        assembler_factory=planner_factory,
    )
    host.start()
    try:
        out = _drive(host, {0: both}, {0: 0.0})
    finally:
        host.close()
    assert out[0].ok and {r.category for r in out[0].requests} == {Category.AADHAAR, Category.PAN}


def test_missing_ocr_models_fail_the_lane_run_safe(tmp_path) -> None:
    cfg = _cfg()
    res = VisionResources(cfg, cache_dir=tmp_path)  # no weights in tmp_path
    host = vhost.VisionHost(
        cfg, InProcessBus(), registry={"cards": DETECTOR}, resources=res, assembler_factory=planner_factory
    )
    with pytest.raises(OcrUnavailableError):
        host.start()  # the warm-up of the cards detector loads OCR: fail at start, not silently at run time
    host.close()


def test_ocr_raising_at_run_time_marks_the_run_not_ok() -> None:
    def boom(frame: Frame) -> OcrResult:
        raise OcrUnavailableError("down")

    cfg = _cfg()
    host = vhost.VisionHost(
        cfg,
        InProcessBus(),
        registry={"cards": DETECTOR},
        resources=VisionResources(cfg, ocr=boom),
        assembler_factory=planner_factory,
    )
    host.start()
    try:
        out = _drive(host, {0: make_negative_scene("desk", 1, 43)}, {0: 0.0})
    finally:
        host.close()
    assert out[0].ok is False and out[0].requests == ()


@pytest.mark.slow
@pytest.mark.skipif(not WEIGHTS, reason="OCR weights not on disk (run models/prepare.py --profile dev)")
def test_acceptance_t13_specimen_cards_recall_precision_latency_cpu() -> None:
    """T1.3: >= 60 augmented SPECIMEN samples through the real host; recall >= 95 %, CPU lane <= 450 ms."""
    scenes = augmented_set(per_kind=36, negatives_per_kind=6, seed=77)
    result = run_acceptance(scenes, "cpu")
    print(result)
    assert result["positives"] >= 60
    assert result["lane_failures"] == 0
    assert result["recall_masked"] >= 0.95 and result["recall_covered_90"] >= 0.95, result["misses"]
    assert result["precision"] >= 0.95 and result["false_positive_frames"] == 0
    lat = result["latency_ms"]
    assert lat["p50"] <= 450.0, lat
    assert lat["p95"] <= 450.0 * 1.5, (
        lat
    )  # p95 slack for a shared dev machine; the budget is on p50/p95 reported
