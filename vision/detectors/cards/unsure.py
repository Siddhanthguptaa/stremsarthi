"""Card-shaped fallback (``mask_when_unsure``): an ID-1 rectangle with text-like texture and no confident reading.

Used when OCR could not read a number on a card (heavy blur, darkness, compression, a hand over it). The quad
comes from the same contour search as ``find_card_like`` (``quad.py``); unlike it, no minimum of OCR boxes is
needed, because on a heavy frame OCR finds none: the evidence is the card's proportions, a real border and
writing-like texture (text bands) inside. A rectangle that holds confident readings is not unsure (it was read
and was not an Aadhaar or PAN number), so it is left alone. Nothing is read here.
"""

from __future__ import annotations

import cv2
import numpy as np

from vision.ocr.layout import poly_area
from vision.shapes import oriented_patch, read_inside, text_bands

from .quad import MIN_EDGE_CONTRAST, _aspect, _dedupe, card_candidates, edge_contrast, quad_overlap

ASPECT = (1.3, 1.95)
MIN_AREA_FRAC, MAX_AREA_FRAC = 0.012, 0.35
MIN_BANDS = 2


def find_card_shapes(
    image: np.ndarray, boxes: list[np.ndarray], texts: list[str], confs: list[float]
) -> list[tuple[np.ndarray, int]]:
    """(quad, text bands) of ID-1 rectangles that look written-on and were not read."""
    h, w = image.shape[:2]
    scale = 0.5
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    min_area = MIN_AREA_FRAC * h * w * scale * scale
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    out: list[tuple[np.ndarray, int]] = []
    for q_small in _dedupe(card_candidates(small, (0, 0, small.shape[1], small.shape[0]), min_area)):
        q = (q_small / scale).astype(np.float32)
        if not ASPECT[0] <= _aspect(q) <= ASPECT[1] or poly_area(q) > MAX_AREA_FRAC * h * w:
            continue
        if (q <= 4).any() or (q[:, 0] >= w - 4).any() or (q[:, 1] >= h - 4).any():
            continue
        if edge_contrast(image, q) < MIN_EDGE_CONTRAST:
            continue
        if read_inside(q, boxes, texts, confs) >= 2:
            continue
        patch = oriented_patch(gray, q, 192, 120)
        if patch is None:
            continue
        bands, _share = text_bands(patch)
        if bands < MIN_BANDS:
            continue
        out.append((q, bands))
    kept: list[tuple[np.ndarray, int]] = []
    for q, n in sorted(out, key=lambda t: -poly_area(t[0])):
        if all(quad_overlap(q, k) < 0.6 for k, _ in kept):
            kept.append((q, n))
    return kept
