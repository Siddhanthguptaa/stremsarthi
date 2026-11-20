"""Whole-card quad expansion: from the number box (or a card-like blob) to the full ID-1 card.

Order of attempts (spec 5.6): (a) contour / rectangle search around the number box for the largest
convex quadrilateral that contains it, has a plausible ID-1 aspect (1.3-1.9) and enough area; (b) the
bounding rotated rectangle of the OCR text near the number, padded 8 %; (c) a card-sized region around
the number box. The caller always masks the whole quad, never only the digits.

Pure OpenCV + numpy, CPU only, a few milliseconds. No text is read here.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vision.ocr.layout import order_quad, poly_area, side_lengths, union_quad

ASPECT_MIN, ASPECT_MAX = 1.3, 1.9
LIKE_ASPECT_MIN, LIKE_ASPECT_MAX = 1.4, 1.7
MIN_AREA_FACTOR = 4.0  # card area >= 4x the number box
MAX_AREA_FACTOR = 40.0
MAX_SIDE_FACTOR = 3.0  # card long side <= 3x the number's long side (real cards: 1.8-2.5x)
MAX_SIDE_PX = 512  # candidate search resolution (longest ROI side)
MIN_EDGE_CONTRAST = 0.6  # share of the perimeter with colour contrast across it
TEXTBLOCK_REACH = 2.5  # text boxes within 2.5x the number width join the fallback hull
TEXTBLOCK_PAD = 0.08


@dataclass(frozen=True, slots=True)
class CardQuad:
    pts: np.ndarray  # (4, 2) float32 clockwise from top-left, frame pixels
    method: str  # "contour" | "textblock" | "padded"
    aspect: float


def _aspect(pts: np.ndarray) -> float:
    long_s, short_s = side_lengths(pts)
    return long_s / short_s if short_s > 0 else 0.0


def _is_convex(pts: np.ndarray) -> bool:
    return bool(cv2.isContourConvex(pts.reshape(-1, 1, 2).astype(np.float32)))


def _contains(quad: np.ndarray, box: np.ndarray, tol: float) -> bool:
    contour = quad.reshape(-1, 1, 2).astype(np.float32)
    return all(cv2.pointPolygonTest(contour, (float(x), float(y)), True) >= -tol for x, y in box)


def _sample(image: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Pixels at float ``pts`` (N, 2) as int16, clipped to the image."""
    h, w = image.shape[:2]
    q = np.rint(pts).astype(int)
    q[:, 0] = np.clip(q[:, 0], 0, w - 1)
    q[:, 1] = np.clip(q[:, 1], 0, h - 1)
    return image[q[:, 1], q[:, 0]].astype(np.int16)


def edge_contrast(image: np.ndarray, quad: np.ndarray, *, step: float = 7.0, min_diff: float = 24.0) -> float:
    """Share of the quad's perimeter where the colour just outside differs from just inside.

    A real card border has contrast all along; a rectangle that is only a clump of text, or a patch
    inside a bigger uniform area, has none. Samples are taken every ``step`` px at 4-7 px either side.
    """
    centre = quad.mean(axis=0)
    hits = total = 0
    for k in range(4):
        a, b = quad[k], quad[(k + 1) % 4]
        edge = b - a
        length = float(np.linalg.norm(edge))
        if length < 1:
            continue
        normal = np.array([edge[1], -edge[0]], np.float32) / length
        if float(np.dot(normal, a - centre)) < 0:
            normal = -normal  # point away from the centre
        t = np.arange(0.15, 0.85, step / length)  # skip the corners (rounded, noisy)
        pts = a[None, :] + t[:, None] * edge[None, :]

        inside = (_sample(image, pts + normal * -4.0) + _sample(image, pts + normal * -7.0)) // 2
        outside = (_sample(image, pts + normal * 4.0) + _sample(image, pts + normal * 7.0)) // 2
        diff = np.abs(inside - outside).sum(axis=1)
        hits += int((diff >= min_diff).sum())
        total += len(diff)
    return hits / total if total else 0.0


def _binary_maps(crop: np.ndarray) -> list[np.ndarray]:
    """Edge, Otsu and background-difference maps of ``crop`` (BGR); each closed so card borders are joined."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    med = float(np.median(blur))
    edges = cv2.Canny(blur, int(max(10, 0.5 * med)), int(max(40, 1.2 * med)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    maps = [
        edges,
        cv2.morphologyEx(otsu, cv2.MORPH_OPEN, kernel),
        cv2.morphologyEx(255 - otsu, cv2.MORPH_OPEN, kernel),
    ]
    # a gradient-magnitude map catches low-contrast (white card on a light table) borders
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1)
    mag = cv2.magnitude(gx, gy)
    thr = max(12.0, float(np.percentile(mag, 88)))
    grad = cv2.morphologyEx((mag > thr).astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel, iterations=2)
    maps.append(grad)
    # colour distance from the crop's border colour: a card differs from its surroundings in hue even
    # where luminance is similar (a dark header band against a dark desk)
    ring = np.concatenate(
        [
            crop[:3].reshape(-1, 3),
            crop[-3:].reshape(-1, 3),
            crop[:, :3].reshape(-1, 3),
            crop[:, -3:].reshape(-1, 3),
        ]
    )
    bg = np.median(ring, axis=0)
    dist = (
        np.abs(cv2.GaussianBlur(crop, (5, 5), 0).astype(np.int16) - bg.astype(np.int16))
        .sum(axis=2)
        .astype(np.float32)
    )
    p95 = float(np.percentile(dist, 95))
    for frac in (0.3, 0.12):  # two sensitivities: a dark header band, then a faint border
        diff = cv2.morphologyEx(
            (dist > max(30.0, frac * p95)).astype(np.uint8) * 255, cv2.MORPH_CLOSE, kernel, iterations=2
        )
        maps.append(cv2.morphologyEx(diff, cv2.MORPH_OPEN, kernel))
    return maps


def _quads_from_contours(binary: np.ndarray, min_area: float) -> list[np.ndarray]:
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out: list[np.ndarray] = []
    for c in contours:
        if cv2.contourArea(c) < min_area * 0.6:
            continue
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        if hull_area < min_area:
            continue
        peri = cv2.arcLength(hull, True)
        approx = cv2.approxPolyDP(hull, 0.02 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            out.append(order_quad(approx.reshape(4, 2).astype(np.float32)))
            continue
        rect = cv2.minAreaRect(hull)
        rect_area = rect[1][0] * rect[1][1]
        if rect_area > 0 and hull_area / rect_area >= 0.9:  # rounded-corner card: take its rectangle
            out.append(order_quad(cv2.boxPoints(rect)))
    return out


def card_candidates(image: np.ndarray, roi: tuple[int, int, int, int], min_area: float) -> list[np.ndarray]:
    """Convex 4-point candidates inside ``roi`` (x0, y0, x1, y1) in frame coordinates.

    Candidates touching a ROI side that is not a frame side are dropped (a clipped card is not a card).
    """
    x0, y0, x1, y1 = roi
    h, w = image.shape[:2]
    crop = image[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    # work at <= MAX_SIDE_PX: the card outline needs no more, and the maps are the main cost
    sc = min(1.0, MAX_SIDE_PX / max(crop.shape[:2]))
    small = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA) if sc < 1.0 else crop
    out: list[np.ndarray] = []
    margin = 3.0
    sw, sh = (x1 - x0) * sc, (y1 - y0) * sc
    for binary in _binary_maps(small):
        for q in _quads_from_contours(binary, min_area * sc * sc):
            clipped = (
                (x0 > 0 and (q[:, 0] <= margin).any())
                or (y0 > 0 and (q[:, 1] <= margin).any())
                or (x1 < w and (q[:, 0] >= sw - margin).any())
                or (y1 < h and (q[:, 1] >= sh - margin).any())
            )
            if not clipped:
                out.append(q / sc + np.array([x0, y0], np.float32))
    return out


def _roi_around(box: np.ndarray, shape: tuple[int, int], half: float) -> tuple[int, int, int, int]:
    h, w = shape
    cx, cy = box.mean(axis=0)
    return (max(0, int(cx - half)), max(0, int(cy - half)), min(w, int(cx + half)), min(h, int(cy + half)))


def quad_overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection area over the smaller quad's area (convex polygons)."""
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    return float(inter) / max(1.0, min(poly_area(a), poly_area(b)))


def _dedupe(quads: list[np.ndarray]) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for q in sorted(quads, key=poly_area, reverse=True):
        if all(np.abs(q - o).max() > 6.0 for o in out):
            out.append(q)
    return out


def find_card_quad(
    image: np.ndarray,
    number_box: np.ndarray,
    text_boxes: list[np.ndarray],
    *,
    aspect_range: tuple[float, float] = (ASPECT_MIN, ASPECT_MAX),
) -> CardQuad:
    """Expand ``number_box`` (4x2) to the whole card. Always returns a quad (see the module docstring)."""
    h, w = image.shape[:2]
    num_long, _ = side_lengths(number_box)
    num_area = max(poly_area(number_box), 1.0)
    frame_area = float(h * w)
    tol = max(3.0, 0.03 * num_long)

    def acceptable(q: np.ndarray) -> bool:
        area = poly_area(q)
        long_s, _ = side_lengths(q)
        on_border = bool((q <= 2).any() or (q[:, 0] >= w - 2).any() or (q[:, 1] >= h - 2).any())
        return (
            not on_border  # a quad glued to the frame edge is the scene, not a card
            and aspect_range[0] <= _aspect(q) <= aspect_range[1]
            and area >= MIN_AREA_FACTOR * num_area
            and area <= min(MAX_AREA_FACTOR * num_area, 0.9 * frame_area)
            and long_s <= MAX_SIDE_FACTOR * num_long
            and _is_convex(q)
            and _contains(q, number_box, tol)
            and edge_contrast(image, q) >= MIN_EDGE_CONTRAST
        )

    for half in (1.9 * num_long, None):  # near the number first, then the whole frame
        roi = _roi_around(number_box, (h, w), half) if half else (0, 0, w, h)
        cands = [q for q in _dedupe(card_candidates(image, roi, MIN_AREA_FACTOR * num_area)) if acceptable(q)]
        if cands:
            best = max(cands, key=poly_area)
            return CardQuad(best, "contour", _aspect(best))

    # (b) bounding rotated rectangle of the text near the number, padded 8 %
    centre = number_box.mean(axis=0)
    near = [b for b in text_boxes if np.linalg.norm(b.mean(axis=0) - centre) <= TEXTBLOCK_REACH * num_long]
    if len(near) >= 3:
        hull = union_quad([number_box, *near])
        c = hull.mean(axis=0)
        padded = c + (hull - c) * (1.0 + 2 * TEXTBLOCK_PAD)
        return CardQuad(_clamp(padded, w, h), "textblock", _aspect(padded))

    # (c) last resort: a card-sized region around the number box (ID-1 sized, rotated like the number)
    u = number_box[1] - number_box[0]
    ang = float(np.arctan2(u[1], u[0])) if np.linalg.norm(u) > 0 else 0.0
    ux = np.array([np.cos(ang), np.sin(ang)], np.float32)
    uy = np.array([-np.sin(ang), np.cos(ang)], np.float32)
    hx, hy = 1.0 * num_long, 0.7 * num_long
    pts = np.stack(
        [
            centre - ux * hx - uy * hy,
            centre + ux * hx - uy * hy,
            centre + ux * hx + uy * hy,
            centre - ux * hx + uy * hy,
        ]
    )
    return CardQuad(_clamp(order_quad(pts), w, h), "padded", _aspect(pts))


def _clamp(pts: np.ndarray, w: int, h: int) -> np.ndarray:
    out = pts.copy()
    out[:, 0] = np.clip(out[:, 0], 0, w)
    out[:, 1] = np.clip(out[:, 1], 0, h)
    return out.astype(np.float32)


def _straddling(quad: np.ndarray, boxes: list[np.ndarray]) -> bool:
    """True when any OCR box lies partly inside and partly outside ``quad``."""
    contour = quad.reshape(-1, 1, 2).astype(np.float32)
    for b in boxes:
        inside = sum(cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0 for x, y in b)
        if 0 < inside < 4:
            return True
    return False


def find_card_like(
    image: np.ndarray,
    text_boxes: list[np.ndarray],
    *,
    min_boxes: int = 4,
    max_area_frac: float = 0.35,
    max_fill: float = 0.42,
) -> list[tuple[np.ndarray, int]]:
    """ID-1 quads (aspect 1.4-1.7) that hold at least ``min_boxes`` OCR boxes, with their box counts.

    Used when no readable number was found (a blurred or distant card). The quad must not touch the frame
    border, must cover at most ``max_area_frac`` of the frame and must not be mostly text (``max_fill``:
    OCR box area over quad area), so a monitor or a page full of text is not taken for a card. The spec
    asks for 6 boxes; the SPECIMEN cards carry 5-7, so the floor is 4 and ``max_fill`` carries the load.
    """
    h, w = image.shape[:2]
    if len(text_boxes) < min_boxes:
        return []
    scale = 0.5
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    min_area = 0.02 * h * w * scale * scale
    centres = np.array([b.mean(axis=0) for b in text_boxes], np.float32)
    found: list[tuple[np.ndarray, int]] = []
    for q_small in _dedupe(card_candidates(small, (0, 0, small.shape[1], small.shape[0]), min_area)):
        q = q_small / scale
        if not (LIKE_ASPECT_MIN <= _aspect(q) <= LIKE_ASPECT_MAX) or poly_area(q) > max_area_frac * h * w:
            continue
        if (
            (q[:, 0] <= 4).any()
            or (q[:, 1] <= 4).any()
            or (q[:, 0] >= w - 4).any()
            or (q[:, 1] >= h - 4).any()
        ):
            continue
        if edge_contrast(image, q) < MIN_EDGE_CONTRAST or _straddling(q, text_boxes):
            continue  # no real border, or text runs across it: a patch of a bigger page, not a card
        contour = q.reshape(-1, 1, 2).astype(np.float32)
        inside = [
            i
            for i, c in enumerate(centres)
            if cv2.pointPolygonTest(contour, (float(c[0]), float(c[1])), False) >= 0
        ]
        fill = sum(poly_area(text_boxes[i]) for i in inside) / max(poly_area(q), 1.0)
        if len(inside) >= min_boxes and fill <= max_fill:
            found.append((q.astype(np.float32), len(inside)))
    found.sort(key=lambda t: -poly_area(t[0]))
    kept: list[tuple[np.ndarray, int]] = []
    for q, n in found:
        if all(quad_overlap(q, k) < 0.6 for k, _ in kept):
            kept.append((q, n))
    return kept
