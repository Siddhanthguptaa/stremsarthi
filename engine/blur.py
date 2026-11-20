"""Polygon-masked blur, pixelate and solid fills, all on ROI crops (never a full-frame pass per quad).

Adapted from PrivaStream ``src/models/detection/blur_utils.py`` (Apache-2.0, Saximn/privastream;
licence text in ``engine/THIRD_PARTY_LICENSES/privastream/LICENSE``) and from IRL Shield
``shield/render.py`` (MIT, Copyright (c) 2026 Elan Nash; ``engine/THIRD_PARTY_LICENSES/irl-shield``).

Kept: the three paint modes (gaussian, pixelate, fill) and the polygon-in-ROI approach. Changed:
the polygon mask is rasterised on the ROI crop only (upstream allocated a full-frame mask per
polygon and blended through float32 3-channel arrays); a 3 px feather that never shrinks the
covered polygon (the mask is grown by 2 px before it is softened); kernel and block sizes follow
docs/specs/engine.md section 5.5; blur is OpenCV's stack blur (a Gaussian approximation whose cost does not grow with the kernel);
the quad is replaced by its convex hull so a bad projection can only cover more, never less.
"""

from __future__ import annotations

import cv2
import numpy as np

from contracts import Category, MaskStyle

SOLID_BGR: tuple[int, int, int] = (32, 32, 32)
FEATHER_PX = 3
STRONG_CATEGORIES: frozenset[Category] = frozenset({Category.AADHAAR, Category.PAN, Category.UPI_QR})
MIN_STRONG_KERNEL = 51
_GROW = 2  # px the polygon is grown by before feathering, so alpha is ~1 on the original edge
_GROW_KERNEL = np.ones((3, 3), np.uint8)


def odd(n: float) -> int:
    value = int(round(n))
    return value if value % 2 == 1 else value + 1


def blur_kernel(width: float, height: float, category: Category | None = None, min_kernel: int = 0) -> int:
    """``odd(max(15, 0.25 * min(w, h)))``, at least 51 for card and QR categories (or ``min_kernel``)."""
    kernel = odd(max(15.0, 0.25 * min(width, height)))
    if category in STRONG_CATEGORIES:
        kernel = max(kernel, MIN_STRONG_KERNEL)
    return max(kernel, odd(min_kernel)) if min_kernel else kernel


def pixelate_block(width: float, height: float) -> int:
    return int(max(8, min(width, height) // 8))


def quad_polygon(points: np.ndarray, width: int, height: int) -> np.ndarray:
    """Convex hull of the four corners as int32 ``(n, 2)``, coordinates bounded for rasterising."""
    pts = np.clip(np.asarray(points, np.float64), -2.0 * width, 3.0 * width)
    pts[:, 1] = np.clip(pts[:, 1], -2.0 * height, 3.0 * height)
    hull = cv2.convexHull(np.rint(pts).astype(np.int32))
    return hull.reshape(-1, 2)


def polygon_roi(poly: np.ndarray, width: int, height: int, margin: int) -> tuple[int, int, int, int] | None:
    x1 = max(0, int(poly[:, 0].min()) - margin)
    y1 = max(0, int(poly[:, 1].min()) - margin)
    x2 = min(width, int(poly[:, 0].max()) + 1 + margin)
    y2 = min(height, int(poly[:, 1].max()) + 1 + margin)
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def gaussian_blur_roi(roi: np.ndarray, kernel: int) -> np.ndarray:
    """Blur ``roi`` with a Gaussian-like kernel of the given odd size.

    Uses OpenCV's stack blur (a fast Gaussian approximation, cost independent of the kernel size):
    a 71 px kernel on a card-sized crop takes about 0.4 ms where ``GaussianBlur`` takes 6 ms.
    """
    return cv2.stackBlur(roi, (kernel, kernel))


def pixelate_roi(roi: np.ndarray, block: int) -> np.ndarray:
    h, w = roi.shape[:2]
    small = cv2.resize(roi, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def paint_polygon(
    out: np.ndarray,
    points: np.ndarray,
    style: MaskStyle,
    category: Category | None = None,
    *,
    feather: int = FEATHER_PX,
    solid_bgr: tuple[int, int, int] = SOLID_BGR,
    min_kernel: int = 0,
) -> bool:
    """Paint one quad onto ``out`` in place. Returns False when it lies outside the frame.

    Only the quad's bounding box (plus a few pixels) is touched: the mask, the blur and the blend
    all run on that crop. ``SOLID`` is a hard fill (no feather) so no source pixel can bleed through.
    """
    height, width = out.shape[:2]
    poly = quad_polygon(points, width, height)
    if len(poly) < 3:
        return False
    box = polygon_roi(poly, width, height, margin=feather + _GROW + 1)
    if box is None:
        return False
    x1, y1, x2, y2 = box
    roi = out[y1:y2, x1:x2]
    local = poly - (x1, y1)
    if style is MaskStyle.SOLID:
        # Direct fill plus an outline that grows it by _GROW px: no mask, no source pixel left at the edge.
        cv2.fillConvexPoly(roi, local, solid_bgr)
        cv2.polylines(roi, [local], True, solid_bgr, thickness=2 * _GROW + 1)
        return True
    mask = np.zeros(roi.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, local, 255)
    mask = cv2.dilate(mask, _GROW_KERNEL, iterations=_GROW)
    bw = float(poly[:, 0].max() - poly[:, 0].min() + 1)
    bh = float(poly[:, 1].max() - poly[:, 1].min() + 1)
    if style is MaskStyle.PIXELATE:
        content = pixelate_roi(roi, pixelate_block(bw, bh))
    else:
        content = gaussian_blur_roi(roi, blur_kernel(bw, bh, category, min_kernel))
    if feather > 0:
        size = feather | 1  # two box passes of ~3 px: a triangular edge, far cheaper than a Gaussian
        alpha = cv2.blur(cv2.blur(mask, (size, size)), (size, size)).astype(np.float32) * (1.0 / 255.0)
        blended = cv2.blendLinear(content, roi, alpha, 1.0 - alpha)
        roi[:] = blended
    else:
        cv2.copyTo(content, mask, roi)
    return True


def polygon_union_ratio(polys: list[np.ndarray], width: int, height: int, scale: int = 8) -> float:
    """Fraction of the frame covered by the union of ``polys`` (computed on a 1/``scale`` canvas)."""
    small_w, small_h = max(1, width // scale), max(1, height // scale)
    canvas = np.zeros((small_h, small_w), np.uint8)
    for poly in polys:
        cv2.fillConvexPoly(canvas, np.rint(poly / scale).astype(np.int32), 255)
    return float(np.count_nonzero(canvas)) / float(canvas.size)
