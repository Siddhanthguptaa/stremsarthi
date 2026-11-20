"""The ``cards`` detector with a fake OCR provider (CPU, no models): logic, privacy, fail-safe."""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

from contracts import (
    Category,
    Config,
    DetectContext,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    OcrResult,
    Quad,
)
from vision.detectors.cards import DETECTOR
from vision.patterns import verhoeff
from vision.resources import OcrUnavailableError, VisionResources
from vision.testing import Scene, make_card_scene, make_negative_scene, scene_ocr


def _cfg(**cats: dict) -> Config:
    cfg = Config(profile="dev", _env_file=None)
    for name, update in cats.items():
        cat = Category(name)
        cfg.categories[cat] = cfg.categories[cat].model_copy(update=update)
    return cfg


def _detector(cfg: Config, ocr) -> object:
    res = VisionResources(cfg, ocr=ocr)
    det = DETECTOR()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU), res)
    return det


def _run(det, scene: Scene, seq: int = 0):
    ctx = DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    return det.detect(Frame(seq, seq / 30.0, 0.0, scene.image), ctx)


def _coverage(det_quad: Quad, truth: np.ndarray) -> float:
    a = np.zeros((720, 1280), np.uint8)
    b = np.zeros_like(a)
    cv2.fillConvexPoly(a, np.array(det_quad.pts, np.int32), 1)
    cv2.fillConvexPoly(b, truth.astype(np.int32), 1)
    return int((a & b).sum()) / max(1, int(b.sum()))


def _split(quad: np.ndarray, n: int) -> list[Quad]:
    """The number box cut into ``n`` boxes along its long side (a grouped number read as separate boxes)."""
    out = []
    for i in range(n):
        t0, t1 = i / n + 0.01, (i + 1) / n - 0.01
        top = quad[0] + (quad[1] - quad[0]) * np.array([[t0], [t1]])
        bot = quad[3] + (quad[2] - quad[3]) * np.array([[t0], [t1]])
        pts = [top[0], top[1], bot[1], bot[0]]
        out.append(Quad(tuple((float(x), float(y)) for x, y in pts)))  # type: ignore[arg-type]
    return out


def _flip_last_digit(number: str) -> str:
    d = number[-1]
    return number[:-1] + str((int(d) + 3) % 10)


def test_class_declaration_matches_the_plugin_contract() -> None:
    assert DETECTOR.id == "cards" and DETECTOR.lane is Lane.ACCURATE
    assert DETECTOR.categories == frozenset({Category.AADHAAR, Category.PAN})
    assert DETECTOR.vram_mb > 0 and DETECTOR.device is Device.GPU_OPTIONAL


def test_aadhaar_number_masks_the_whole_card() -> None:
    sc = make_card_scene("aadhaar", 1, 21, tilt=9.0, width_frac=0.4)
    assert verhoeff.is_valid(sc.number_text.replace(" ", ""))
    det = _detector(_cfg(), scene_ocr(sc))
    out = _run(det, sc)
    assert len(out) == 1
    d = out[0]
    assert d.category is Category.AADHAAR and d.detector == "cards" and d.confidence >= 0.88
    assert d.extra["reason"] == "verhoeff_ok" and d.extra["expansion"] == "contour"
    assert _coverage(d.quad, sc.card_quad) >= 0.95  # the card, not only the digits
    assert cv2.contourArea(np.array(d.quad.pts, np.float32)) > 4 * cv2.contourArea(
        sc.number_quad.astype(np.float32)
    )


def test_number_split_over_three_boxes_still_validates() -> None:
    sc = make_card_scene("aadhaar", 2, 22, tilt=-7.0)
    groups = sc.number_text.split()
    boxes = _split(sc.number_quad, 3)
    ocr = lambda f: OcrResult(tuple(boxes), tuple(groups), (0.97,) * 3, ("latin",) * 3)  # noqa: E731
    out = _run(_detector(_cfg(), ocr), sc)
    assert [d.extra["reason"] for d in out] == ["verhoeff_ok"]
    assert _coverage(out[0].quad, sc.card_quad) >= 0.95


def test_ocr_confusions_inside_a_digit_field_are_repaired() -> None:
    sc = make_card_scene("aadhaar", 3, 23)
    mangled = sc.number_text.replace("0", "O").replace("1", "l")
    out = _run(_detector(_cfg(), scene_ocr(sc, number_text=mangled)), sc)
    assert len(out) == 1 and out[0].extra["reason"] == "verhoeff_ok"


def test_misread_digit_is_masked_as_card_shape_unsure_only_with_mask_when_unsure() -> None:
    sc = make_card_scene("aadhaar", 4, 24)
    bad = _flip_last_digit(sc.number_text)
    assert not verhoeff.is_valid(bad.replace(" ", ""))
    on = _run(_detector(_cfg(), scene_ocr(sc, number_text=bad)), sc)
    assert len(on) == 1 and on[0].extra["reason"] == "card_shape_unsure"
    assert on[0].confidence < 0.7 and _coverage(on[0].quad, sc.card_quad) >= 0.95
    off_cfg = _cfg(aadhaar={"mask_when_unsure": False})
    assert _run(_detector(off_cfg, scene_ocr(sc, number_text=bad, with_text=False)), sc) == []


def test_pan_pattern_and_repair() -> None:
    sc = make_card_scene("pan", 5, 25, tilt=-5.0)
    out = _run(_detector(_cfg(), scene_ocr(sc)), sc)
    assert len(out) == 1 and out[0].category is Category.PAN and out[0].extra["reason"] == "pan_pattern"
    assert _coverage(out[0].quad, sc.card_quad) >= 0.95
    fixed = sc.number_text[:5] + sc.number_text[5:9].replace("0", "O") + sc.number_text[9]
    out2 = _run(_detector(_cfg(), scene_ocr(sc, number_text=fixed)), sc)
    assert len(out2) == 1 and out2[0].category is Category.PAN


def test_card_like_when_the_number_is_unreadable() -> None:
    sc = make_card_scene("aadhaar", 6, 26, tilt=3.0, width_frac=0.4, occlude_number=True)
    ocr = lambda f: OcrResult(  # noqa: E731
        tuple(Quad(tuple((float(x), float(y)) for x, y in q)) for q in sc.text_quads),  # type: ignore[arg-type]
        ("SPECIMEN LINE",) * len(sc.text_quads),
        (0.9,) * len(sc.text_quads),
        ("latin",) * len(sc.text_quads),
    )
    out = _run(_detector(_cfg(), ocr), sc)
    assert len(out) == 1 and out[0].category is Category.AADHAAR
    assert out[0].extra["reason"] == "card_like" and out[0].confidence == pytest.approx(0.5)
    off = _run(_detector(_cfg(aadhaar={"mask_when_unsure": False}), ocr), sc)
    assert off == []
    plain = make_negative_scene("plain_rect", 0, 26)
    empty = lambda f: OcrResult((), (), (), ())  # noqa: E731
    assert _run(_detector(_cfg(), empty), plain) == []


def test_already_masked_aadhaar_card_is_not_card_like_masked() -> None:
    sc = make_card_scene("aadhaar", 7, 27, tilt=2.0, width_frac=0.4, occlude_number=True)
    boxes = [Quad(tuple((float(x), float(y)) for x, y in q)) for q in sc.text_quads]  # type: ignore[arg-type]
    texts = ["XXXX XXXX 1234"] + ["SPECIMEN LINE"] * (len(boxes) - 1)
    ocr = lambda f: OcrResult(tuple(boxes), tuple(texts), (0.9,) * len(boxes), ("latin",) * len(boxes))  # noqa: E731
    assert _run(_detector(_cfg(), ocr), sc) == []


def test_disabled_categories_are_skipped_without_running_ocr() -> None:
    sc = make_card_scene("aadhaar", 8, 28)

    def boom(frame):
        raise AssertionError("OCR must not run when both categories are off")

    cfg = _cfg(aadhaar={"enabled": False}, pan={"enabled": False})
    assert _run(_detector(cfg, boom), sc) == []
    only_pan = _cfg(aadhaar={"enabled": False})
    assert _run(_detector(only_pan, scene_ocr(sc)), sc) == []  # an Aadhaar number with Aadhaar off


def test_ocr_failure_propagates_so_the_lane_fails_safe() -> None:
    sc = make_card_scene("pan", 9, 29)

    def broken(frame):
        raise OcrUnavailableError("models missing")

    with pytest.raises(OcrUnavailableError):
        _run(_detector(_cfg(), broken), sc)


def test_empty_frame_gives_no_detection() -> None:
    sc = make_negative_scene("desk", 0, 30)
    empty = lambda f: OcrResult((), (), (), ())  # noqa: E731
    assert _run(_detector(_cfg(), empty), sc) == []


def test_privacy_no_digits_in_detection_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    sc = make_card_scene("aadhaar", 10, 31)
    digits = sc.number_text.replace(" ", "")
    caplog.set_level(logging.DEBUG)
    out = _run(_detector(_cfg(), scene_ocr(sc)), sc)
    assert out
    for d in out:
        blob = repr(d) + repr(dict(d.extra))
        assert digits not in blob and sc.number_text not in blob
        assert all(isinstance(v, (int, float, bool, str)) for v in d.extra.values())
        assert not any(ch.isdigit() for v in d.extra.values() if isinstance(v, str) for ch in v)
    assert digits not in caplog.text and sc.number_text not in caplog.text


def test_registry_discovers_cards_and_no_detector_imports_a_sibling() -> None:
    from pathlib import Path

    from vision import registry

    found = registry.discover()
    assert "cards" in found and found["cards"] is DETECTOR
    assert registry.check_no_cross_imports(Path(registry.__file__).parent / "detectors") == []
