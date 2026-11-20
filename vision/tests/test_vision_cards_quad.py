"""Whole-card quad expansion on SPECIMEN scenes (CPU, OpenCV only, no OCR models)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from vision.detectors.cards.quad import edge_contrast, find_card_like, find_card_quad
from vision.testing import make_card_scene, make_negative_scene

CANVAS = (720, 1280)


def _coverage(found: np.ndarray, truth: np.ndarray) -> tuple[float, float]:
    """(share of the true card covered by ``found``, IoU)."""
    a = np.zeros(CANVAS, np.uint8)
    b = np.zeros(CANVAS, np.uint8)
    cv2.fillConvexPoly(a, found.astype(np.int32), 1)
    cv2.fillConvexPoly(b, truth.astype(np.int32), 1)
    inter = int((a & b).sum())
    return inter / max(1, int(b.sum())), inter / max(1, int((a | b).sum()))


@pytest.mark.parametrize("kind", ["aadhaar", "pan"])
@pytest.mark.parametrize("tilt", [-15.0, -8.0, 0.0, 6.0, 15.0])
def test_contour_search_covers_the_whole_card_when_tilted(kind: str, tilt: float) -> None:
    sc = make_card_scene(kind, 1, 5, tilt=tilt, width_frac=0.42)
    card = find_card_quad(sc.image, sc.number_quad, sc.text_quads)
    assert card.method == "contour"
    cov, iou = _coverage(card.pts, sc.card_quad)
    assert cov >= 0.95 and iou >= 0.85, (cov, iou)
    assert 1.3 <= card.aspect <= 1.9


@pytest.mark.parametrize("kind", ["aadhaar", "pan"])
@pytest.mark.parametrize("width_frac", [0.22, 0.3, 0.5])
def test_contour_search_at_different_distances(kind: str, width_frac: float) -> None:
    sc = make_card_scene(kind, 2, 6, tilt=7.0, width_frac=width_frac)
    card = find_card_quad(sc.image, sc.number_quad, sc.text_quads)
    assert card.method == "contour"
    assert _coverage(card.pts, sc.card_quad)[0] >= 0.95


@pytest.mark.parametrize("kind", ["aadhaar", "pan"])
def test_card_quad_with_partial_blur_glare_and_jpeg(kind: str) -> None:
    for i, extra in enumerate([{"partial_blur": 2.5}, {"glare": True}, {"jpeg": 50}, {"blur": 1.5}]):
        sc = make_card_scene(kind, 10 + i, 7, **extra)
        card = find_card_quad(sc.image, sc.number_quad, sc.text_quads)
        assert card.method == "contour", extra
        assert _coverage(card.pts, sc.card_quad)[0] >= 0.9, extra


def test_whole_card_is_masked_not_only_the_digits() -> None:
    sc = make_card_scene("aadhaar", 3, 8)
    card = find_card_quad(sc.image, sc.number_quad, sc.text_quads)
    card_area = cv2.contourArea(card.pts.astype(np.float32))
    number_area = cv2.contourArea(sc.number_quad.astype(np.float32))
    assert card_area > 5 * number_area


def test_fallback_text_block_when_no_card_edge_exists() -> None:
    sc = make_card_scene("aadhaar", 4, 9, tilt=5.0)
    flat = np.full_like(sc.image, 120)  # no edges anywhere: the card outline is not recoverable
    card = find_card_quad(flat, sc.number_quad, sc.text_quads)
    assert card.method == "textblock"
    inside = [
        cv2.pointPolygonTest(card.pts.astype(np.float32), (float(x), float(y)), False) >= 0
        for x, y in sc.number_quad
    ]
    assert all(inside)
    assert cv2.contourArea(card.pts.astype(np.float32)) > 3 * cv2.contourArea(
        sc.number_quad.astype(np.float32)
    )


def test_last_resort_padded_region_contains_the_number_box() -> None:
    sc = make_card_scene("pan", 5, 10)
    flat = np.full_like(sc.image, 90)
    card = find_card_quad(flat, sc.number_quad, [])
    assert card.method == "padded"
    assert all(
        cv2.pointPolygonTest(card.pts.astype(np.float32), (float(x), float(y)), False) >= 0
        for x, y in sc.number_quad
    )
    assert card.pts[:, 0].min() >= 0 and card.pts[:, 0].max() <= 1280 and card.pts[:, 1].max() <= 720


def test_candidates_glued_to_a_bigger_scene_are_rejected() -> None:
    """A bright half of the frame is a big blob that contains the number: it is not a card."""
    sc = make_card_scene("aadhaar", 6, 11, tilt=0.0, width_frac=0.3, clutter=False)
    img = sc.image.copy()
    img[:, :200] = 250  # bright band on the left edge, no relation to the card
    card = find_card_quad(img, sc.number_quad, sc.text_quads)
    assert card.method == "contour" and _coverage(card.pts, sc.card_quad)[0] >= 0.95
    assert card.pts[:, 0].min() > 150


def test_edge_contrast_separates_a_card_border_from_a_patch_of_uniform_area() -> None:
    sc = make_card_scene("pan", 7, 12, tilt=3.0)
    assert edge_contrast(sc.image, sc.card_quad) > 0.9
    patch = np.array([[100, 100], [300, 100], [300, 220], [100, 220]], np.float32)
    assert edge_contrast(np.full_like(sc.image, 128), patch) == 0.0


def test_card_like_fires_on_a_card_with_unreadable_number_but_not_on_a_plain_rectangle() -> None:
    sc = make_card_scene("aadhaar", 8, 13, tilt=4.0, width_frac=0.4, occlude_number=True)
    boxes = [sc.number_quad, *sc.text_quads]
    found = find_card_like(sc.image, [np.asarray(b, np.float32) for b in boxes])
    assert len(found) == 1
    assert _coverage(found[0][0], sc.card_quad)[0] >= 0.9
    plain = make_negative_scene("plain_rect", 1, 13)
    assert find_card_like(plain.image, []) == []
    assert (
        find_card_like(plain.image, [np.asarray(b, np.float32) for b in boxes]) == []
    )  # boxes but no card outline


def test_card_like_ignores_a_frame_filling_screen_of_text() -> None:
    screen = make_negative_scene("text_screen", 2, 14)
    # eight fake text lines laid across the panel: dense text, large area
    boxes = [
        np.array(
            [[300, 120 + 45 * i], [900, 120 + 45 * i], [900, 150 + 45 * i], [300, 150 + 45 * i]], np.float32
        )
        for i in range(8)
    ]
    assert find_card_like(screen.image, boxes) == []
