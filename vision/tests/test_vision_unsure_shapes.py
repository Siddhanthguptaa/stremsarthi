"""T7.5: the ``mask_when_unsure`` shape fallbacks of cards, plates and screens (CPU, fake OCR, synthetic frames).

Everything drawn here is a synthetic stand-in (a plate-shaped rectangle with bars, a card-shaped rectangle with text
lines, a banner): no real document, no real plate, no real data.
"""

from __future__ import annotations

import cv2
import numpy as np

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
from vision import shapes
from vision.detectors.cards import DETECTOR as CARDS
from vision.detectors.plates import DETECTOR as PLATES
from vision.detectors.screens import DETECTOR as SCREENS
from vision.resources import VisionResources

EMPTY = OcrResult((), (), (), ())


def _soft(img: np.ndarray, sigma: float = 4.0, gain: float = 0.5) -> np.ndarray:
    """A heavy-level frame: blurred, darkened, noisy (what OCR cannot read)."""
    out = cv2.GaussianBlur(img, (0, 0), sigma).astype(np.float32) * gain
    rng = np.random.default_rng(3)
    out += rng.normal(0, 2.0, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def _scene() -> np.ndarray:
    img = np.full((720, 1280, 3), 70, np.uint8)
    cv2.circle(img, (200, 150), 60, (90, 80, 120), -1)
    return img


def _plate(img: np.ndarray, x: int = 500, y: int = 400, w: int = 220, h: int = 60) -> np.ndarray:
    cv2.rectangle(img, (x, y), (x + w, y + h), (225, 225, 225), -1)
    n = 9
    step = (w - 30) / n
    for i in range(n):  # one bar per character
        x0 = int(x + 15 + i * step)
        cv2.rectangle(img, (x0, y + 14), (x0 + int(step * 0.5), y + h - 14), (20, 20, 20), -1)
    return img


def _card(img: np.ndarray, x: int = 400, y: int = 200, w: int = 400, h: int = 250) -> np.ndarray:
    cv2.rectangle(img, (x, y), (x + w, y + h), (230, 230, 230), -1)
    for i in range(5):  # text lines
        yy = y + 40 + i * 40
        for j in range(12):
            xx = x + 30 + j * 28 + (i % 2) * 6
            cv2.rectangle(img, (xx, yy), (xx + 18, yy + 14), (25, 25, 25), -1)
    return img


def _banner(img: np.ndarray, x: int = 300, y: int = 300, w: int = 360, h: int = 70) -> np.ndarray:
    cv2.rectangle(img, (x, y), (x + w, y + h), (235, 235, 235), -1)
    for i in range(3):
        for j in range(14):
            xx = x + 14 + j * 23
            cv2.rectangle(img, (xx, y + 10 + i * 18), (xx + 14, y + 18 + i * 18), (30, 30, 30), -1)
    return img


def _setup(det_cls, category: Category, ocr=EMPTY, **params):
    cfg = Config(profile="dev", _env_file=None)
    res = VisionResources(cfg, ocr=lambda _f: ocr)
    det = det_cls()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU, params=params), res)
    return det, cfg


def _run(det, image: np.ndarray):
    return det.detect(Frame(0, 0.0, 0.0, image), DetectContext(lane=Lane.ACCURATE, categories={}, stride=1))


def test_degraded_separates_a_soft_frame_from_a_sharp_one() -> None:
    sharp = _plate(_scene())
    assert not shapes.degraded(sharp)
    assert shapes.degraded(_soft(sharp))


def test_plate_and_card_fallbacks_are_off_by_default_and_the_banner_one_is_on() -> None:
    det, _ = _setup(PLATES, Category.PLATE)
    assert _run(det, _soft(_plate(_scene()), sigma=2.0, gain=0.6)) == []
    det, _ = _setup(CARDS, Category.AADHAAR)
    assert _run(det, _soft(_card(_scene()), sigma=2.5, gain=0.6)) == []
    det, _ = _setup(SCREENS, Category.OTP_SCREEN)  # T7.5 measurements: banner ON, relaxed templates OFF
    banner = _soft(_banner(_scene()), sigma=1.5, gain=0.7)
    assert [d.extra["reason"] for d in _run(det, banner)] == ["banner_shape_unsure"]
    det, _ = _setup(SCREENS, Category.OTP_SCREEN, shape_fallback=False)
    assert _run(det, banner) == []


def test_plate_shape_is_masked_when_unreadable_and_enabled() -> None:
    img = _soft(_plate(_scene()), sigma=2.0, gain=0.6)
    det, _ = _setup(PLATES, Category.PLATE, shape_fallback=True)
    dets = _run(det, img)
    assert dets, "a soft plate-shaped rectangle with character bars is masked when unsure"
    d = dets[0]
    assert d.category is Category.PLATE and d.extra["reason"] == "plate_shape_unsure"
    assert 0.5 <= d.confidence < 0.7
    x1, y1, x2, y2 = d.quad.bbox()
    assert x1 < 520 and x2 > 700 and y1 < 420 and y2 > 440


def test_plate_shape_needs_the_flag_the_category_switch_and_a_degraded_frame() -> None:
    soft = _soft(_plate(_scene()), sigma=2.0, gain=0.6)
    sharp = _plate(_scene())
    det, cfg = _setup(PLATES, Category.PLATE, shape_fallback=True)
    assert _run(det, sharp) == []  # sharp frame: OCR would have read it, no fallback
    cfg.categories[Category.PLATE] = cfg.categories[Category.PLATE].model_copy(
        update={"mask_when_unsure": False}
    )
    det.configure(
        DetectorConfig(categories=cfg.categories, device=Device.CPU, params={"shape_fallback": True}),
        det._res,
    )
    assert _run(det, soft) == []  # mask_when_unsure off for plates
    det.set_shape_fallback(False)
    cfg.categories[Category.PLATE] = cfg.categories[Category.PLATE].model_copy(
        update={"mask_when_unsure": True}
    )
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU, params={}), det._res)
    assert _run(det, soft) == []  # flag off


def test_a_plate_shape_with_a_confident_reading_is_not_unsure() -> None:
    img = _soft(_plate(_scene()), sigma=2.0, gain=0.6)
    read = OcrResult((Quad.from_xyxy(520, 415, 700, 445),), ("WELCOME HOME",), (0.95,), ("latin",))
    det, _ = _setup(PLATES, Category.PLATE, ocr=read, shape_fallback=True)
    assert _run(det, img) == []


def test_card_shape_is_masked_as_aadhaar_when_unreadable() -> None:
    img = _soft(_card(_scene()), sigma=2.5, gain=0.6)
    det, _ = _setup(CARDS, Category.AADHAAR, shape_fallback=True)
    dets = _run(det, img)
    assert dets and dets[0].extra["reason"] == "card_shape_texture"
    assert dets[0].category is Category.AADHAAR
    x1, y1, x2, y2 = dets[0].quad.bbox()
    assert x1 < 420 and x2 > 780 and y1 < 220 and y2 > 430


def test_banner_shape_is_masked_as_otp_screen_when_unreadable() -> None:
    img = _soft(_banner(_scene()), sigma=1.5, gain=0.7)
    det, _ = _setup(SCREENS, Category.OTP_SCREEN, shape_fallback=True, template_fallback=False)
    dets = [d for d in _run(det, img) if d.category is Category.OTP_SCREEN]
    assert dets and dets[0].extra["reason"] == "banner_shape_unsure"


def test_detections_never_carry_text_or_pixels() -> None:
    img = _soft(_plate(_scene()), sigma=2.0, gain=0.6)
    det, _ = _setup(PLATES, Category.PLATE, shape_fallback=True)
    for d in _run(det, img):
        assert set(d.extra) <= {"reason", "layout", "strokes"}
        assert all(isinstance(v, (str, int, float)) for v in d.extra.values())


def test_clean_scene_without_shapes_yields_nothing() -> None:
    img = _soft(_scene())
    for cls, cat in ((PLATES, Category.PLATE), (CARDS, Category.AADHAAR), (SCREENS, Category.OTP_SCREEN)):
        det, _ = _setup(cls, cat, shape_fallback=True)
        assert _run(det, img) == []
