"""Shape / texture helpers for the ``mask_when_unsure`` fallbacks of cards, plates and screens (T7.5).

These run when OCR read too little to trust (heavy blur, darkness, compression). They never read text: they look for
a rectangle of the right proportions whose inside looks like writing (stroke columns for a plate, text bands for a
card or a notification banner). Pure OpenCV + numpy, CPU, a few milliseconds at ``WORK_WIDTH``.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vision.ocr.layout import order_quad, poly_area, side_lengths

WORK_WIDTH = 640


@dataclass(frozen=True, slots=True)
class Rect:
    quad: np.ndarray  # (4, 2) float32 clockwise from top-left, FRAME pixels
    aspect: float  # long / short
    fill: float  # contour area / rotated-rectangle area


def work_image(image: np.ndarray, width: int = WORK_WIDTH) -> tuple[np.ndarray, float]:
    """(grayscale image at <= ``width`` px wide, scale work/frame)."""
    h, w = image.shape[:2]
    s = min(1.0, width / float(w))
    small = cv2.resize(image, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else image
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), s


def _binary_maps(gray: np.ndarray) -> list[np.ndarray]:
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    maps = [cv2.morphologyEx(otsu, cv2.MORPH_OPEN, k)]
    # local bright: brighter than its neighbourhood (a light card or plate on a dark or mid-tone scene)
    local = cv2.GaussianBlur(gray, (0, 0), max(6.0, gray.shape[1] / 40.0))
    bright = ((blur.astype(np.int16) - local.astype(np.int16)) > 14).astype(np.uint8) * 255
    maps.append(cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
    # a gain-normalised copy catches a dark frame (brightness 0.35): stretch, then Otsu again
    lo, hi = np.percentile(blur, (2, 99.5))
    if hi - lo > 6:
        st = np.clip((blur.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
        _, o2 = cv2.threshold(st, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        maps.append(cv2.morphologyEx(o2, cv2.MORPH_OPEN, k))
    return maps


def find_rects(
    gray: np.ndarray,
    scale: float,
    *,
    min_area: float,
    max_area: float,
    aspect: tuple[float, float],
    min_fill: float = 0.8,
    close: tuple[int, int] = (1, 1),
) -> list[Rect]:
    """Rotated rectangles (frame pixels) of bright regions with the given area (frame px^2) and aspect."""
    out: list[Rect] = []
    for m in _binary_maps(gray):
        if close != (1, 1):
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((close[1], close[0]), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            a = cv2.contourArea(c) / (scale * scale)
            if a < 0.5 * min_area or a > max_area * 1.2:
                continue
            rect = cv2.minAreaRect(c)
            ra = rect[1][0] * rect[1][1] / (scale * scale)
            if not min_area <= ra <= max_area:
                continue
            lo, sh = max(rect[1]), min(rect[1])
            if sh <= 0 or not aspect[0] <= lo / sh <= aspect[1]:
                continue
            fill = float(cv2.contourArea(c)) / max(rect[1][0] * rect[1][1], 1.0)
            if fill < min_fill:
                continue
            quad = order_quad(cv2.boxPoints(rect).astype(np.float32) / scale)
            out.append(Rect(quad, lo / sh, fill))
    return dedupe_rects(out)


def dedupe_rects(rects: list[Rect], tol: float = 0.6) -> list[Rect]:
    kept: list[Rect] = []
    for r in sorted(rects, key=lambda r: -poly_area(r.quad)):
        if all(_overlap(r.quad, k.quad) < tol for k in kept):
            kept.append(r)
    return kept


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    return float(inter) / max(1.0, min(poly_area(a), poly_area(b)))


def oriented_patch(gray_full: np.ndarray, quad: np.ndarray, width: int, height: int) -> np.ndarray | None:
    """The quad warped to ``width`` x ``height`` with its LONG side horizontal (gray in, gray out)."""
    pts = order_quad(quad.astype(np.float32))
    e0 = float(np.linalg.norm(pts[1] - pts[0]))
    e1 = float(np.linalg.norm(pts[2] - pts[1]))
    if min(e0, e1) < 3:
        return None
    if e0 < e1:  # long side is the second edge: rotate the corner order
        pts = np.roll(pts, -1, axis=0)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32)
    m = cv2.getPerspectiveTransform(pts, dst)
    return cv2.warpPerspective(gray_full, m, (width, height), flags=cv2.INTER_AREA)


def stroke_columns(patch: np.ndarray) -> tuple[int, float]:
    """(number of stroke columns, dark share) of a plate-like patch: a one-row run of characters gives a
    column-darkness profile with one bump per glyph (about 8-10 on a plate, fewer when smeared)."""
    p = cv2.GaussianBlur(patch, (3, 3), 0).astype(np.float32)
    inner = p[
        int(0.15 * p.shape[0]) : int(0.85 * p.shape[0]), int(0.06 * p.shape[1]) : int(0.94 * p.shape[1])
    ]
    if inner.size == 0:
        return 0, 0.0
    lo, hi = float(np.percentile(inner, 8)), float(np.percentile(inner, 92))
    span = hi - lo
    if span < 18:  # no contrast at all: nothing written here
        return 0, 0.0
    dark = np.clip((hi - inner) / span, 0, 1)
    prof = cv2.GaussianBlur(dark.mean(axis=0).reshape(1, -1), (0, 0), 1.2).ravel()
    thr = float(prof.mean() + 0.25 * (prof.max() - prof.min()))
    peaks = 0
    for i in range(1, len(prof) - 1):
        if prof[i] > thr and prof[i] >= prof[i - 1] and prof[i] > prof[i + 1]:
            peaks += 1
    return peaks, float((dark > 0.5).mean())


def text_bands(patch: np.ndarray) -> tuple[int, float]:
    """(number of text-line bands, share of the patch rows that are textured) of a card or banner patch."""
    p = cv2.GaussianBlur(patch, (3, 3), 0).astype(np.float32)
    gx = np.abs(cv2.Sobel(p, cv2.CV_32F, 1, 0, ksize=3))
    rows = gx.mean(axis=1)
    hi = float(rows.max())
    if hi < 6.0:
        return 0, 0.0
    rows = cv2.GaussianBlur(rows.reshape(-1, 1), (1, 0), 1.0).ravel()
    thr = max(5.0, float(rows.mean()) + 0.15 * (hi - float(rows.mean())))
    mask = rows > thr
    bands, run = 0, 0
    for v in mask:
        if v:
            run += 1
        else:
            if run >= 2:
                bands += 1
            run = 0
    if run >= 2:
        bands += 1
    return bands, float(mask.mean())


def long_short(quad: np.ndarray) -> tuple[float, float]:
    return side_lengths(quad)


def read_inside(
    quad: np.ndarray, boxes: list[np.ndarray], texts: list[str], confs: list[float], *, min_conf: float = 0.75
) -> int:
    """How many OCR boxes whose centre lies inside ``quad`` hold a confident reading of 2+ letters or digits.

    A shape with a confident reading inside is not "unsure": whatever it shows was read and was not the target.
    (Only counts boxes; the text itself is never kept.)
    """
    contour = quad.reshape(-1, 1, 2).astype(np.float32)
    n = 0
    for b, t, c in zip(boxes, texts, confs, strict=False):
        if c < min_conf or sum(ch.isalnum() for ch in t) < 2:
            continue
        cx, cy = b.mean(axis=0)
        if cv2.pointPolygonTest(contour, (float(cx), float(cy)), False) >= 0:
            n += 1
    return n


def overlap(a: np.ndarray, b: np.ndarray) -> float:
    return _overlap(a, b)


DEGRADED_LAPVAR = 75.0  # Laplacian variance of the 640 px grayscale frame: clean 264+ (p5), light 58+ (p5), heavy <= 83 (p95)


def sharpness(image: np.ndarray) -> float:
    """Laplacian variance of the frame at 640 px wide (one cheap number, about 1 ms): low = blurred, dark or smeared."""
    gray, _s = work_image(image)
    return float(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F).var())


def degraded(image: np.ndarray, limit: float = DEGRADED_LAPVAR) -> bool:
    """True when the frame is too soft or dark for OCR-first detectors to be trusted (the unsure fallbacks only run then)."""
    return sharpness(image) < limit
