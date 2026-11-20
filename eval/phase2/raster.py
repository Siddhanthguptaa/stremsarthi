"""Polygon coverage on the 1280x720 canvas: how much of a target quad is under a set of mask quads.

Masks are drawn into a boolean canvas once per frame (``MaskCanvas``); each target is then rasterised inside
its own bounding box only, so a frame with a dozen targets costs a few small fills.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import cv2
import numpy as np

W, H = 1280, 720
Pts = Sequence[Sequence[float]]


def _poly(points: Pts) -> np.ndarray:
    return np.round(np.asarray(points, dtype=np.float64)).astype(np.int32).reshape(-1, 1, 2)


class MaskCanvas:
    """Union of filled polygons, clipped to the frame."""

    def __init__(self) -> None:
        self.canvas = np.zeros((H, W), dtype=np.uint8)
        self.n = 0

    def add(self, points: Pts) -> None:
        cv2.fillPoly(self.canvas, [_poly(points)], 1)
        self.n += 1

    def add_all(self, quads: Iterable[Pts]) -> None:
        for q in quads:
            self.add(q)

    def fill_frame(self) -> None:
        self.canvas[:] = 1
        self.n += 1

    def coverage(self, points: Pts) -> float:
        """Fraction of the polygon's area (inside the frame) that is covered. 0 for an empty canvas."""
        return coverage_on(self.canvas, points)


def coverage_on(canvas: np.ndarray, points: Pts) -> float:
    arr = np.asarray(points, dtype=np.float64)
    x1 = max(0, int(np.floor(arr[:, 0].min())))
    y1 = max(0, int(np.floor(arr[:, 1].min())))
    x2 = min(canvas.shape[1], int(np.ceil(arr[:, 0].max())) + 1)
    y2 = min(canvas.shape[0], int(np.ceil(arr[:, 1].max())) + 1)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    shifted = np.round(arr - np.array([x1, y1])).astype(np.int32).reshape(-1, 1, 2)
    tgt = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
    cv2.fillPoly(tgt, [shifted], 1)
    total = int(tgt.sum())
    if total == 0:
        return 0.0
    return float((tgt & canvas[y1:y2, x1:x2]).sum()) / total


def area_inside(canvas_union: np.ndarray, points: Pts) -> float:
    """Same as ``coverage_on`` (a mask's share that lies inside a union of ground-truth regions)."""
    return coverage_on(canvas_union, points)


def bbox_iou(a: Pts, b: Pts) -> float:
    aa, bb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    ax1, ay1, ax2, ay2 = aa[:, 0].min(), aa[:, 1].min(), aa[:, 0].max(), aa[:, 1].max()
    bx1, by1, bx2, by2 = bb[:, 0].min(), bb[:, 1].min(), bb[:, 0].max(), bb[:, 1].max()
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return float(inter / union) if union > 0 else 0.0
