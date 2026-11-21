"""Plate-shaped fallback (``mask_when_unsure``): a rectangle that looks like a number plate, read by shape only.

Used when OCR returned no plate reading (heavy blur, darkness, compression). A candidate is a bright (or
yellow) rectangle with a one-line (2:1 to 5:1) or two-line (about 1.2:1 to 2:1) plate aspect and a plausible
size, whose inside has a row of stroke columns (one bump per character) and a dark share like printed
characters. Nothing is read; confidence is low (0.55) so the planner masks it for its short hold and lets it
go when it is not seen again.
"""

from __future__ import annotations

import numpy as np

from vision.ocr.layout import poly_area
from vision.shapes import Rect, find_rects, oriented_patch, stroke_columns, work_image

CONF = 0.56
MIN_AREA_PX, MAX_AREA_FRAC = 700.0, 0.03  # frame px^2 / share of the frame
ONE_LINE, TWO_LINE = (2.0, 5.6), (1.15, 2.0)
MIN_PEAKS_ONE, MIN_PEAKS_ROW_TWO = 5, 3
DARK_SHARE = (0.06, 0.55)


def find_plate_shapes(image: np.ndarray) -> list[tuple[np.ndarray, str, int]]:
    """(quad in frame pixels, layout, stroke columns) of every plate-looking rectangle."""
    h, w = image.shape[:2]
    gray_small, s = work_image(image)
    full_gray = None
    rects = find_rects(
        gray_small,
        s,
        min_area=MIN_AREA_PX,
        max_area=MAX_AREA_FRAC * h * w,
        aspect=(TWO_LINE[0], ONE_LINE[1]),
        min_fill=0.78,
        close=(3, 1),
    )
    out: list[tuple[np.ndarray, str, int]] = []
    for r in rects:
        if _at_border(r, w, h):
            continue
        if full_gray is None:
            full_gray = _gray(image)
        res = _check(full_gray, r)
        if res is not None:
            out.append((r.quad, res[0], res[1]))
    return out


def _gray(image: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _at_border(r: Rect, w: int, h: int) -> bool:
    q = r.quad
    return bool((q <= 1).any() or (q[:, 0] >= w - 2).any() or (q[:, 1] >= h - 2).any())


def _check(gray: np.ndarray, r: Rect) -> tuple[str, int] | None:
    if ONE_LINE[0] <= r.aspect <= ONE_LINE[1]:
        patch = oriented_patch(gray, r.quad, 132, 32)
        if patch is not None:
            peaks, dark = stroke_columns(patch)
            if peaks >= MIN_PEAKS_ONE and DARK_SHARE[0] <= dark <= DARK_SHARE[1]:
                return "one_line", peaks
    if TWO_LINE[0] <= r.aspect <= TWO_LINE[1]:
        patch = oriented_patch(gray, r.quad, 96, 64)
        if patch is not None:
            top, bottom = patch[:32], patch[32:]
            p1, d1 = stroke_columns(top)
            p2, d2 = stroke_columns(bottom)
            if min(p1, p2) >= MIN_PEAKS_ROW_TWO and DARK_SHARE[0] <= (d1 + d2) / 2 <= DARK_SHARE[1]:
                return "two_line", p1 + p2
    return None


def area_frac(quad: np.ndarray, image: np.ndarray) -> float:
    return poly_area(quad) / float(image.shape[0] * image.shape[1])
