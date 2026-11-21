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
    whole_frame: bool = True,
) -> CardQuad:
    """Expand ``number_box`` (4x2) to the whole card. Always returns a quad (see the module docstring).

    ``whole_frame=False`` skips the second, whole-frame contour search (about 20-35 ms): the caller uses it for
    unconfirmed hits, which are kept only when a card outline is found right around the number."""
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

    for half in (
        (1.9 * num_long, None) if whole_frame else (1.9 * num_long,)
    ):  # near the number, then the frame
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


PARTIAL_ASPECT = (0.7, ASPECT_MIN)  # a card with a part hidden: the visible region is squarer than ID-1
ID1_ASPECT = 1.58  # the card's own long/short ratio, used to infer the hidden part
FLUSH_PX = 10.0  # an OCR box this close to a side of the visible region is cut by it


def _cut_sides(visible: np.ndarray, u: np.ndarray, boxes: list[np.ndarray]) -> tuple[bool, bool]:
    """(lo, hi): whether text is flush against the visible region's low / high end along ``u``.

    Text laid out inside a real card edge keeps a margin; text cut by an occluder runs right up to the
    occluder's edge. Counting the boxes that touch each end tells which end is the hidden one."""
    contour = visible.reshape(-1, 1, 2).astype(np.float32)
    proj = visible @ u
    lo_e, hi_e = float(proj.min()), float(proj.max())
    lo_n = hi_n = 0
    for b in boxes:
        c = b.mean(axis=0)
        if cv2.pointPolygonTest(contour, (float(c[0]), float(c[1])), False) < 0:
            continue
        pb = b @ u
        lo_n += int(float(pb.min()) - lo_e <= FLUSH_PX)
        hi_n += int(hi_e - float(pb.max()) <= FLUSH_PX)
    return lo_n >= 2 and lo_n > hi_n, hi_n >= 2 and hi_n > lo_n


def find_partial_card_quad(
    image: np.ndarray, number_box: np.ndarray, text_boxes: list[np.ndarray], *, half_number: bool = False
) -> CardQuad | None:
    """The whole card quad inferred from a card that is partly hidden (a hand, a phone, another window).

    Finds the convex region around the number whose aspect is squarer than ID-1 (so the plain search
    refuses it), works out which end is cut from the text that runs flush against it (both ends when
    undecided) and grows that end until the quad reaches the ID-1 aspect. ``half_number`` says the visible
    number is 8 of 12 digits: the hidden digits and the visible side's margin then give a second estimate,
    and the larger growth wins. Returns None when no visible card-like region holds the number."""
    h, w = image.shape[:2]
    num_long, _ = side_lengths(number_box)
    num_area = max(poly_area(number_box), 1.0)
    tol = max(3.0, 0.12 * num_long)  # the number's label box may overhang a clipped region
    roi = _roi_around(number_box, (h, w), 1.9 * num_long)
    best: np.ndarray | None = None
    for q in _dedupe(card_candidates(image, roi, MIN_AREA_FACTOR * num_area)):
        area = poly_area(q)
        on_border = bool((q <= 2).any() or (q[:, 0] >= w - 2).any() or (q[:, 1] >= h - 2).any())
        if (
            on_border
            or not (PARTIAL_ASPECT[0] <= _aspect(q) <= PARTIAL_ASPECT[1])
            or area < MIN_AREA_FACTOR * num_area
            or area > min(MAX_AREA_FACTOR * num_area, 0.5 * h * w)
            or not _is_convex(q)
            or not _contains(q, number_box, tol)
            or edge_contrast(image, q) < MIN_EDGE_CONTRAST
        ):
            continue
        if best is None or area > poly_area(best):
            best = q
    if best is None:
        return None
    # the card's horizontal axis follows the number's baseline
    e = number_box[1] - number_box[0]
    ang = float(np.arctan2(e[1], e[0])) if np.linalg.norm(e) > 0 else 0.0
    u = np.array([np.cos(ang), np.sin(ang)], np.float32)
    v = np.array([-np.sin(ang), np.cos(ang)], np.float32)
    u_pr, v_pr = best @ u, best @ v
    u0, u1 = float(u_pr.min()), float(u_pr.max())
    v0, v1 = float(v_pr.min()), float(v_pr.max())
    # the colour map can miss a header band: text inside the region says how tall the card really is
    contour = best.reshape(-1, 1, 2).astype(np.float32)
    for b in text_boxes:
        c = b.mean(axis=0)
        if cv2.pointPolygonTest(contour, (float(c[0]), float(c[1])), False) >= 0:
            pb = b @ v
            v0, v1 = min(v0, float(pb.min())), max(v1, float(pb.max()))
    pad_v = 0.10 * (v1 - v0)
    v0, v1 = v0 - pad_v, v1 + pad_v
    width, height = u1 - u0, v1 - v0
    need = ID1_ASPECT * height - width
    if need <= 0:
        return None
    lo, hi = _cut_sides(best, u, text_boxes)
    grow_lo, grow_hi = (lo, hi) if (lo or hi) else (True, True)
    new_u0 = u0 - need if grow_lo else u0
    new_u1 = u1 + need if grow_hi else u1
    if half_number and (lo != hi):
        nu = number_box @ u
        n0, n1 = float(nu.min()), float(nu.max())
        hidden = 0.5 * (n1 - n0)  # 4 of 12 digits are hidden
        if lo:
            new_u0 = min(new_u0, n0 - hidden - max(0.0, u1 - n1))
        else:
            new_u1 = max(new_u1, n1 + hidden + max(0.0, n0 - u0))
    pts = np.array(
        [new_u0 * u + v0 * v, new_u1 * u + v0 * v, new_u1 * u + v1 * v, new_u0 * u + v1 * v], np.float32
    )  # (u, v) is orthonormal, so the coordinates map straight back
    return CardQuad(_clamp(order_quad(pts), w, h), "partial", _aspect(pts))


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


_LIKE_SCALE = 0.5


def region_candidates(image: np.ndarray) -> list[np.ndarray]:
    """Card-like convex regions of the whole frame at half resolution, in the HALF-resolution frame.

    The expensive part of ``find_card_like`` and ``find_edge_card``: compute once, pass to both."""
    h, w = image.shape[:2]
    small = cv2.resize(image, None, fx=_LIKE_SCALE, fy=_LIKE_SCALE, interpolation=cv2.INTER_AREA)
    min_area = 0.02 * h * w * _LIKE_SCALE * _LIKE_SCALE
    return _dedupe(card_candidates(small, (0, 0, small.shape[1], small.shape[0]), min_area))


PAPER_V, PAPER_S = 215, 25  # card paper: bright and nearly colourless
EDGE_MIN_AREA_FRAC = 0.03  # a card cut by a frame side still shows at least 3 % of the frame
PAPER_MIN_BOXES = 3  # a paper region has no outline of its own: it must hold real text


def _paper_edge_quads(image: np.ndarray) -> list[np.ndarray]:
    """Bright low-saturation regions glued to one left/right frame side (half-resolution frame coordinates).

    A card sliding in beside a building or a table can have no closed outline of its own (the contour search
    merges it with what is behind it) but its paper is still a clean block against the frame side. The block
    is grown upward and downward to take in the coloured header band and footer strip around the paper."""
    small = cv2.resize(image, None, fx=_LIKE_SCALE, fy=_LIKE_SCALE, interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    paper = ((hsv[..., 2] >= PAPER_V) & (hsv[..., 1] <= PAPER_S)).astype(np.uint8)
    paper = cv2.morphologyEx(paper, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    paper = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(paper, connectivity=4)
    out: list[np.ndarray] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (0.12 * sh <= bh <= 0.6 * sh) or bw < 0.1 * bh or area < 0.6 * bw * bh:
            continue
        if not (x <= 1 or x + bw >= sw - 1) or (x <= 1 and x + bw >= sw - 1):
            continue
        grow_up, grow_dn = 0.2 * bh, 0.06 * bh  # header band above, footer strip below
        out.append(
            np.array(
                [[x, y - grow_up], [x + bw, y - grow_up], [x + bw, y + bh + grow_dn], [x, y + bh + grow_dn]],
                np.float32,
            )
        )
    return out


def find_edge_card(
    image: np.ndarray,
    text_boxes: list[np.ndarray],
    *,
    candidates: list[np.ndarray] | None = None,
    min_boxes: int = 1,
    max_fill: float = 0.42,
) -> list[np.ndarray]:
    """Whole-card quads for a card that is entering or leaving through ONE frame side.

    A card cut by the frame edge is a card-like region glued to exactly one frame side, holding text and with
    a real border on its other three sides. The part outside the frame is inferred from the ID-1 aspect and
    the quad is returned extended beyond the frame (the caller clamps it for display, the planner keeps
    the overshoot so the mask stays on the card as it slides). Never a quad on two sides, a screen filling
    the frame or a page of text."""
    h, w = image.shape[:2]
    if not text_boxes:
        return []  # no text, no card: skip every costly search
    cands = region_candidates(image) if candidates is None else candidates
    paper = _paper_edge_quads(image)
    cands = [*cands, *paper]
    n_paper = len(paper)
    centres = np.array([b.mean(axis=0) for b in text_boxes], np.float32)
    out: list[np.ndarray] = []
    for ci, q_small in enumerate(cands):
        is_paper = ci >= len(cands) - n_paper
        q = q_small / _LIKE_SCALE
        if is_paper:  # exact to the frame side: the grid snap of the half-size block may stop 1 px short
            q[q[:, 0] <= 3, 0] = 0.0
            q[q[:, 0] >= w - 3, 0] = float(w)
        sides = {
            "left": bool((q[:, 0] <= 4).any()),
            "right": bool((q[:, 0] >= w - 4).any()),
            "top": bool((q[:, 1] <= 4).any()),
            "bottom": bool((q[:, 1] >= h - 4).any()),
        }
        touching = [k for k, v in sides.items() if v]
        if len(touching) != 1 or touching[0] in ("top", "bottom"):
            continue  # only horizontal entries/exits: a card is landscape on a landscape stream
        side = touching[0]
        x0, x1 = float(q[:, 0].min()), float(q[:, 0].max())
        y0, y1 = float(q[:, 1].min()), float(q[:, 1].max())
        height, depth = y1 - y0, x1 - x0
        if (
            not (0.16 * h <= height <= 0.8 * h)
            or depth < 0.12 * height
            or depth >= ID1_ASPECT * height * 0.97
            or depth * height < EDGE_MIN_AREA_FRAC * h * w  # a thin strip or small block is not a card
            or height > 6.0 * depth  # (e.g. a barcode stamp in a corner)
        ):
            continue
        if not is_paper and edge_contrast(image, q) < 0.4:
            continue
        contour = q.reshape(-1, 1, 2).astype(np.float32)
        inside = [
            i
            for i, c in enumerate(centres)
            if cv2.pointPolygonTest(contour, (float(c[0]), float(c[1])), False) >= 0
        ]
        fill = sum(poly_area(text_boxes[i]) for i in inside) / max(poly_area(q), 1.0)
        wide = q.copy()  # text running off the frame side is not "across the border"
        if side == "right":
            wide[wide[:, 0] >= w - 4, 0] += 24.0
        else:
            wide[wide[:, 0] <= 4, 0] -= 24.0
        if len(inside) < (PAPER_MIN_BOXES if is_paper else min_boxes) or fill > (
            0.6 if is_paper else max_fill
        ):
            continue
        if _straddling(wide, text_boxes):
            continue
        reach = ID1_ASPECT * height - depth  # how far the card runs on beyond the frame
        pad = 0.03 * height
        if side == "right":
            nx0, nx1 = x0 - pad, x1 + reach
        else:
            nx0, nx1 = x0 - reach, x1 + pad
        out.append(np.array([[nx0, y0 - pad], [nx1, y0 - pad], [nx1, y1 + pad], [nx0, y1 + pad]], np.float32))
    out.sort(key=poly_area, reverse=True)  # one quad per card: the inner cream-only region is a duplicate
    kept: list[np.ndarray] = []
    for q in out:
        if all(quad_overlap(q, k) < 0.6 for k in kept):
            kept.append(q)
    return kept


def find_card_like(
    image: np.ndarray,
    text_boxes: list[np.ndarray],
    *,
    min_boxes: int = 4,
    max_area_frac: float = 0.35,
    max_fill: float = 0.42,
    candidates: list[np.ndarray] | None = None,
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
    scale = _LIKE_SCALE
    centres = np.array([b.mean(axis=0) for b in text_boxes], np.float32)
    found: list[tuple[np.ndarray, int]] = []
    for q_small in region_candidates(image) if candidates is None else candidates:
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
