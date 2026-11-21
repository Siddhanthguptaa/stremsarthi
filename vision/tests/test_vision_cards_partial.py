"""Partly hidden cards and cards cut by a frame side: the whole card is inferred (CPU, no models)."""

from __future__ import annotations

import cv2
import numpy as np

from vision.detectors.cards.quad import find_edge_card, find_partial_card_quad

W, H = 1280, 720


def _scene() -> np.ndarray:
    img = np.full((H, W, 3), (170, 160, 120), np.uint8)
    cv2.rectangle(img, (0, 600), (W, H), (90, 90, 90), -1)
    return img


def _box(x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], np.float32)


def test_partial_card_quad_covers_the_hidden_part() -> None:
    # card (370..910 x 190..530) with its left 40 % under an occluder; two groups of digits visible
    img = _scene()
    cv2.rectangle(img, (370, 190), (910, 530), (240, 244, 245), -1)
    cv2.rectangle(img, (370, 190), (910, 235), (40, 130, 240), -1)
    cv2.rectangle(img, (330, 150), (586, 570), (130, 160, 205), -1)  # the occluder
    num = _box(572, 424, 843, 498)
    texts = [_box(583, 196, 811, 230), _box(574, 250, 702, 285), _box(574, 278, 679, 309), num]
    card = find_partial_card_quad(img, num, texts, half_number=True)
    assert card is not None and card.method == "partial"
    truth = np.array([[370, 190], [910, 190], [910, 530], [370, 530]], np.float32)
    inter, _ = cv2.intersectConvexConvex(card.pts, truth)
    assert inter / cv2.contourArea(truth) >= 0.9, "the inferred quad must cover the whole card"
    assert card.pts[:, 0].min() < 400, "the hidden left end must be included"


def test_partial_card_quad_refuses_a_plain_region() -> None:
    img = _scene()
    cv2.rectangle(img, (500, 200), (700, 520), (240, 244, 245), -1)
    num = _box(900, 300, 1100, 340)  # a number that is not inside any region
    assert find_partial_card_quad(img, num, [num], half_number=True) is None


def test_edge_card_is_extended_beyond_the_frame_side() -> None:
    img = _scene()
    cv2.rectangle(img, (1090, 190), (W, 535), (240, 244, 245), -1)  # right 190 px of a card
    cv2.rectangle(img, (1090, 190), (W, 235), (40, 130, 240), -1)
    box = _box(1212, 395, 1279, 443)
    found = find_edge_card(img, [box])
    assert len(found) == 1
    quad = found[0]
    assert quad[:, 0].max() > W + 200, "the card runs on beyond the frame"
    assert quad[:, 0].min() <= 1095


def test_edge_card_ignores_a_big_screen_and_a_card_in_the_middle() -> None:
    img = _scene()
    cv2.rectangle(img, (400, 190), (940, 535), (240, 244, 245), -1)  # whole card, not on a side
    assert find_edge_card(img, [_box(500, 300, 700, 340)]) == []


def test_cut_pan_pieces_and_edge_card_category() -> None:
    from contracts import Category, Config, DetectorConfig, Device
    from vision.detectors.cards import DETECTOR
    from vision.detectors.cards.detector import _PAN_PART
    from vision.resources import VisionResources

    for piece in ("ABCDE12", "ABCDE1", "AB1234F", "1234F"):
        assert _PAN_PART.match(piece), piece
    for other in ("ABCDEFGHIJ", "HELLO", "1234", "ABCDE1234F"):
        assert not _PAN_PART.match(other), other
    cfg = Config(profile="dev", _env_file=None)
    det = DETECTOR()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU), VisionResources(cfg))
    assert det._edge_category(["INCOME TAX DEPARTMENT", "NAME"]) is Category.PAN
    assert det._edge_category(["ABCDE12"]) is Category.PAN
    assert det._edge_category(["SPECIMEN", "ADDRESS LINE"]) is Category.AADHAAR


def test_cut_pan_partial_covers_the_whole_true_card() -> None:
    """PAN pieces take the aspect-only growth (no 12-digit estimate): the quad must still cover the whole card."""
    img = _scene()
    cv2.rectangle(img, (370, 190), (910, 530), (240, 244, 245), -1)
    cv2.rectangle(img, (370, 190), (910, 235), (150, 120, 60), -1)
    cv2.rectangle(img, (330, 150), (586, 570), (130, 160, 205), -1)  # hides the left 40 %
    num = _box(580, 300, 780, 345)  # "ABCDE12": the rest of the PAN is hidden
    texts = [_box(583, 196, 811, 230), _box(576, 250, 702, 285), _box(578, 380, 700, 410), num]
    card = find_partial_card_quad(img, num, texts, half_number=False)
    assert card is not None
    truth = np.array([[370, 190], [910, 190], [910, 530], [370, 530]], np.float32)
    inter, _ = cv2.intersectConvexConvex(card.pts, truth)
    assert inter / cv2.contourArea(truth) >= 0.9
