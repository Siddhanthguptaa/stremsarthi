"""Geometry helpers over OCR boxes: rotation-aware line grouping and polygon utilities.

Shared by the card, text, plate and screen detectors (they must not import each other). Boxes are
``contracts.Quad`` (clockwise from top-left) or ``numpy`` arrays of shape (4, 2).
"""

from __future__ import annotations

import math

import numpy as np

from contracts import Quad


def as_array(q: Quad) -> np.ndarray:
    return np.asarray(q.pts, dtype=np.float32)


def to_quad(pts: np.ndarray) -> Quad:
    return Quad(tuple((float(x), float(y)) for x, y in pts))  # type: ignore[arg-type]


def poly_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def order_quad(pts: np.ndarray) -> np.ndarray:
    """Four points clockwise (image coordinates) starting at the top-left-most corner."""
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    ordered = pts[np.argsort(ang)]
    start = int(np.argmin(ordered[:, 0] + ordered[:, 1]))
    return np.roll(ordered, -start, axis=0).astype(np.float32)


def side_lengths(pts: np.ndarray) -> tuple[float, float]:
    """(long, short) mean side length of a clockwise quad."""
    a = (np.linalg.norm(pts[0] - pts[1]) + np.linalg.norm(pts[3] - pts[2])) / 2.0
    b = (np.linalg.norm(pts[0] - pts[3]) + np.linalg.norm(pts[1] - pts[2])) / 2.0
    return (float(a), float(b)) if a >= b else (float(b), float(a))


def box_axis(pts: np.ndarray) -> tuple[np.ndarray, float, float]:
    """(unit vector along the long side, long length, short length) of a text box."""
    e1, e2 = pts[1] - pts[0], pts[3] - pts[0]
    long_edge = e1 if np.linalg.norm(e1) >= np.linalg.norm(e2) else e2
    n = float(np.linalg.norm(long_edge))
    u = long_edge / n if n > 0 else np.array([1.0, 0.0], np.float32)
    if u[0] < 0:
        u = -u
    lo, sh = side_lengths(pts)
    return u, lo, sh


def group_lines(
    boxes: list[np.ndarray], *, max_gap_h: float = 2.0, max_perp_h: float = 0.6
) -> list[list[int]]:
    """Group box indices that sit on one text line (any tilt), each group left-to-right along the line.

    Two boxes are on one line when their directions differ by under 12 degrees, their centres are within
    ``max_perp_h`` box heights across the line and the gap between them along the line is at most
    ``max_gap_h`` box heights. Works for a 15-degree tilted card where a 3-group number spans 400 px.
    """
    n = len(boxes)
    info = [box_axis(b) for b in boxes]
    centres = [b.mean(axis=0) for b in boxes]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        ui, li, hi = info[i]
        for j in range(i + 1, n):
            uj, lj, hj = info[j]
            h = (hi + hj) / 2.0
            if h <= 0:
                continue
            cos = float(np.clip(abs(np.dot(ui, uj)), -1.0, 1.0))
            if math.degrees(math.acos(cos)) > 12.0 and min(li / max(hi, 1e-6), lj / max(hj, 1e-6)) > 1.5:
                continue
            d = centres[j] - centres[i]
            along = abs(float(np.dot(d, ui)))
            perp = abs(float(d[0] * ui[1] - d[1] * ui[0]))
            if perp <= max_perp_h * h and along - (li + lj) / 2.0 <= max_gap_h * h:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        u = info[members[0]][0]
        members.sort(key=lambda k: float(np.dot(centres[k], u)))
        out.append(members)
    return out


def union_quad(boxes: list[np.ndarray]) -> np.ndarray:
    """Rotated rectangle (4x2, clockwise) of minimum area around all points of ``boxes``."""
    import cv2

    pts = np.concatenate(boxes, axis=0).astype(np.float32)
    return order_quad(cv2.boxPoints(cv2.minAreaRect(pts)))
