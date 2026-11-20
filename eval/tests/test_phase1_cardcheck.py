"""Card-visibility checker on synthetic masked and unmasked frames (T1.4, gate G1)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from eval.cardcheck import (
    SIM_LEAK,
    CardChecker,
    OcrReader,
    aadhaar_reads,
    is_slate,
    ocr_available,
    read_verdict,
)
from eval.data_gen.common import verhoeff
from eval.sender.scenes import ScenePlayer, sprite_number
from eval.sender.stamp import STAMP_EXCLUSION, draw_stamp

SCRIPT = "card_static:10,card_hold:20"


def _frame(index: int, script: str = SCRIPT):
    player = ScenePlayer(script, 30)
    rgb, scene, targets = player.render(index)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    card = next(t for t in targets if t.category == "aadhaar")
    return bgr, card, scene


def _box(card, pad: int = 12):
    xs = [p[0] for p in card.polygon()]
    ys = [p[1] for p in card.polygon()]
    return int(min(xs)) - pad, int(min(ys)) - pad, int(max(xs)) + pad, int(max(ys)) + pad


def _solid(bgr, card):
    out = bgr.copy()
    x1, y1, x2, y2 = _box(card)
    cv2.rectangle(out, (x1, y1), (x2, y2), (32, 32, 32), -1)
    return out


def _pixelate(bgr, card, cells: int = 6):
    out = bgr.copy()
    x1, y1, x2, y2 = _box(card)
    roi = out[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    small = cv2.resize(roi, (cells, max(2, cells * h // w)), interpolation=cv2.INTER_AREA)
    out[y1:y2, x1:x2] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
    return out


def _blur(bgr, card, sigma: float = 14.0):
    out = bgr.copy()
    x1, y1, x2, y2 = _box(card)
    out[y1:y2, x1:x2] = cv2.GaussianBlur(out[y1:y2, x1:x2], (0, 0), sigma)
    return out


# ---------------------------------------------------------------- structure test


@pytest.mark.parametrize("index", [30, 330, 600, 800])  # static, hold: still, moving, tilted
def test_unmasked_card_is_recoverable(index):
    bgr, card, _ = _frame(index)
    v = CardChecker().check(bgr, card)
    assert v.leaked and v.similar
    assert v.similarity > 0.6, v  # a sharp card correlates at 0.7 to 0.95
    assert v.number_similarity > 0.6, v


@pytest.mark.parametrize("mask", [_solid, _pixelate, _blur])
@pytest.mark.parametrize("index", [30, 800])
def test_masked_card_is_not_recoverable(mask, index):
    bgr, card, _ = _frame(index)
    v = CardChecker().check(mask(bgr, card), card)
    assert not v.leaked, (mask.__name__, v)
    assert v.similarity < SIM_LEAK and v.number_similarity < SIM_LEAK


def test_partial_mask_over_the_number_band_only_still_counts_as_leak_of_the_card_face():
    """Covering only the number leaves the face readable by the whole-card test (the policy is whole card)."""
    bgr, card, _ = _frame(30)
    from eval.sender.scenes import sprite_number_box

    bx = sprite_number_box(card.ref)
    x0, y0 = card.quad[0], card.quad[1]
    out = bgr.copy()
    cv2.rectangle(out, (x0 + bx[0], y0 + bx[1]), (x0 + bx[2], y0 + bx[3]), (0, 0, 0), -1)
    v = CardChecker().check(out, card)
    assert v.similarity > SIM_LEAK  # the rest of the card is intact


def test_no_card_pixels_in_a_different_scene_correlate():
    """The ground-truth quad on a frame that holds no card (absent scene) does not look like the card."""
    bgr, card, _ = _frame(30)
    absent, _, _ = ScenePlayer("card_absent:5", 30).render(30)
    absent_bgr = cv2.cvtColor(absent, cv2.COLOR_RGB2BGR)
    v = CardChecker().check(absent_bgr, card)
    assert not v.leaked


def test_frame_of_another_size_is_scaled():
    bgr, card, _ = _frame(30)
    small = cv2.resize(bgr, (640, 360), interpolation=cv2.INTER_AREA)
    v = CardChecker().check(small, card)
    assert v.similarity > 0.4  # half resolution loses detail but the structure still matches


# ---------------------------------------------------------------- slates


def test_slate_detection():
    flat = np.full((720, 1280, 3), (28, 22, 18), np.uint8)
    assert is_slate(flat)
    texted = flat.copy()
    cv2.putText(
        texted,
        "Privacy hold - checking coverage",
        (260, 360),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (240, 240, 240),
        2,
    )
    assert is_slate(texted)
    with_stamp = flat.copy()
    draw_stamp(with_stamp, 123, 456, False)
    assert is_slate(with_stamp)  # the stamp corner is under 1.5 % of the area
    bgr, _, _ = _frame(30)
    assert not is_slate(bgr)


def test_slate_is_never_a_card():
    flat = np.full((720, 1280, 3), (28, 22, 18), np.uint8)
    _, card, _ = _frame(30)
    v = CardChecker().check(flat, card)
    assert not v.leaked


def test_scene_targets_never_touch_the_stamp():
    x1, y1, x2, y2 = STAMP_EXCLUSION
    player = ScenePlayer("card_static:5,card_hold:30,card_moving:10", 30)
    for i in range(0, 45 * 30, 7):
        _, targets = player.targets(i)
        for t in targets:
            for px, py in t.polygon():
                assert not (x1 <= px <= x2 and y1 <= py <= y2), (i, px, py)


# ---------------------------------------------------------------- reads


def _valid_number(prefix: str = "12345678901") -> str:
    return prefix + str(verhoeff.check_digit(prefix))


def test_read_verdict_accepts_only_verhoeff_valid_reads():
    good = _valid_number()
    assert verhoeff.is_valid(good) and len(good) == 12
    spaced = f"{good[:4]} {good[4:8]} {good[8:]}"
    assert read_verdict([spaced], None) == (True, False)
    assert read_verdict(["Aadhaar", spaced], good) == (True, True)
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert not verhoeff.is_valid(bad)
    assert read_verdict([f"{bad[:4]} {bad[4:8]} {bad[8:]}"], None) == (False, False)
    assert read_verdict([], good) == (False, False)
    assert read_verdict(["1234 5678"], None) == (False, False)  # not 12 digits


def test_reads_split_over_lines_and_with_separators():
    good = _valid_number()
    reads = aadhaar_reads([good[:4], good[4:8], good[8:]])
    assert good in reads  # the joined blob is searched too
    assert good in aadhaar_reads([f"{good[:4]}-{good[4:8]}.{good[8:]}"])
    assert aadhaar_reads(["call 9876543210 now"]) == []  # a phone number is not an Aadhaar read
    assert aadhaar_reads(["1234567890123"]) == []  # 13 digits, no boundary match


def test_sprite_number_is_specimen_valid_and_leading_0_or_1():
    n = sprite_number("aadhaar")
    assert n is not None
    digits = n.replace(" ", "")
    assert verhoeff.is_valid(digits) and digits[0] in "01"


# ---------------------------------------------------------------- OCR (needs rapidocr, a few seconds)


@pytest.mark.slow
@pytest.mark.skipif(not ocr_available(), reason="rapidocr not installed")
def test_ocr_reads_the_unmasked_card_and_nothing_from_the_masked_one():
    bgr, card, _ = _frame(30)
    checker = CardChecker(OcrReader(threads=2))
    open_v = checker.check(bgr, card, run_ocr=True)
    assert open_v.ocr_run and open_v.leaked
    assert open_v.valid_read, "OCR should read the printed SPECIMEN number from the unmasked card"
    assert open_v.exact_read
    for mask in (_solid, _pixelate, _blur):
        v = checker.check(mask(bgr, card), card, run_ocr=True)
        assert v.ocr_run and not v.valid_read, mask.__name__
        assert not v.leaked, mask.__name__
