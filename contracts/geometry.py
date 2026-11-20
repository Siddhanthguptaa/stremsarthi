"""Geometry. All coordinates are pixels on the canonical 1280x720 canvas."""

from __future__ import annotations

import math
from dataclasses import dataclass

CANVAS_W = 1280
CANVAS_H = 720

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Quad:
    """Four corners, clockwise from top-left. An axis-aligned box is the special case."""

    pts: tuple[Point, Point, Point, Point]

    def __post_init__(self) -> None:
        if len(self.pts) != 4:
            raise ValueError("Quad needs exactly 4 points")
        if not all(math.isfinite(c) for pt in self.pts for c in pt):
            raise ValueError("Quad coordinates must be finite")

    @staticmethod
    def from_xyxy(x1: float, y1: float, x2: float, y2: float) -> Quad:
        if x2 < x1 or y2 < y1:
            raise ValueError("xyxy must satisfy x2>=x1 and y2>=y1")
        return Quad(((x1, y1), (x2, y1), (x2, y2), (x1, y2)))

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def center(self) -> Point:
        return (sum(p[0] for p in self.pts) / 4.0, sum(p[1] for p in self.pts) / 4.0)

    def area_bbox(self) -> float:
        x1, y1, x2, y2 = self.bbox()
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)

    def padded(self, px: float) -> Quad:
        """Push every corner `px` away from the centre along each axis.

        Exact for axis-aligned boxes; an approximation for rotated quads (fail-safe side:
        the result always contains the original quad).
        """
        cx, cy = self.center()

        def push(p: Point) -> Point:
            dx = px if p[0] >= cx else -px
            dy = px if p[1] >= cy else -px
            return (p[0] + dx, p[1] + dy)

        a, b, c, d = (push(p) for p in self.pts)
        return Quad((a, b, c, d))

    def clamped(self, width: int = CANVAS_W, height: int = CANVAS_H) -> Quad:
        def clamp(p: Point) -> Point:
            return (min(max(p[0], 0.0), float(width)), min(max(p[1], 0.0), float(height)))

        a, b, c, d = (clamp(p) for p in self.pts)
        return Quad((a, b, c, d))

    def iou(self, other: Quad) -> float:
        """IoU of the two bounding boxes (documented approximation for rotated quads)."""
        ax1, ay1, ax2, ay2 = self.bbox()
        bx1, by1, bx2, by2 = other.bbox()
        iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
        ih = max(0.0, min(ay2, by2) - max(ay1, by1))
        inter = iw * ih
        union = self.area_bbox() + other.area_bbox() - inter
        return inter / union if union > 0 else 0.0
