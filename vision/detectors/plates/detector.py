"""``plates`` detector: Indian number plates, OCR first (ACCURATE lane, spec 5.8, plan D-11).

Flow per frame: the shared OCR pass (``res.ocr``, one pass for cards, text, plates and screens), then

1. Candidate readings: every text box, windows of two to four boxes of one line (``KA 01`` ``AB`` ``1234``)
   and stacked pairs (two-line plates: ``KA 01`` over ``AB 1234``, the lines joined top to bottom).
2. Each candidate must match a plate format as a whole (``formats.match_plate``: standard, single-digit RTO,
   Bharat series, OCR confusions repaired by position).
3. Plate geometry: the text union grown to the plate (border and the blue IND strip), refined to the plate's
   own rectangle when a contour around the text is found. The plate must have a plate-like aspect (2:1 to
   5:1, with a margin for perspective).
4. Optional vehicle gate (``params["vehicle_gate"]``, a callable ``(image, quad) -> bool``): when given it can
   veto a plate that does not sit on a vehicle. Without it a format match alone is accepted (spec 5.8; the
   YOLOX-nano vehicle model has no verified permissive weights yet). YOLO plate weights (AGPL) are not used.

Detections carry codes and numbers only (``extra``: reason, series, layout, repairs, boxes), never the plate
text. Tracking and hold are the planner's job.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

import cv2
import numpy as np

from contracts import (
    Category,
    DetectContext,
    Detection,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    OcrResult,
    Quad,
    Resources,
)
from vision.ocr.layout import (
    as_array,
    box_axis,
    group_lines,
    order_quad,
    poly_area,
    side_lengths,
    to_quad,
    union_quad,
)
from vision.ocr.normalize import normalise_text
from vision.shapes import degraded, read_inside

from .formats import PlateMatch, alnum, match_plate
from .unsure import CONF as UNSURE_CONF
from .unsure import find_plate_shapes

log = logging.getLogger("vision.plates")

CATEGORIES = frozenset({Category.PLATE})
# plate-like aspect with a margin for perspective: one-line plates 2:1 to 5:1 (spec), two-line plates (two-wheelers)
# are nearly square (about 1.3:1 to 1.6:1)
ASPECT = {"one_line": (1.8, 5.6), "two_line": (1.0, 2.3)}
MIN_TEXT_HEIGHT_PX = 8  # a text line shorter than this is not legible: its plate is not readable either
MAX_WINDOW = 4  # boxes of one line joined into one reading
MAX_BOXES = 60  # candidate generation is quadratic: cap on boxes with plate-sized text
STACK_GAP_H = (0.3, 2.2)  # vertical distance between two stacked lines, in line heights (centre to centre)
PAD_ALONG, PAD_ACROSS = 0.55, 0.45  # growth of the text union to the plate, in line heights, per side
MAX_PLATE_AREA = 0.25  # a plate larger than this share of the frame is not a plate
CONTOUR_MIN_GROWTH, CONTOUR_MAX_GROWTH = 1.3, 6.0  # contour area / grown text area
VehicleGate = Callable[[np.ndarray, np.ndarray], bool]
# the shape fallback (``unsure.py``) is OFF by default until T7.5 measurements say otherwise: see docs/STATUS.md
SHAPE_FALLBACK_DEFAULT = False


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    return float(inter) / max(1.0, min(poly_area(a), poly_area(b)))


@dataclass(slots=True)
class _Candidate:
    idx: tuple[int, ...]
    match: PlateMatch
    layout: str  # "one_line" | "two_line"
    lines: int


def _lower_bound_text(text: str) -> bool:
    n = len(alnum(text))
    return 2 <= n <= 11


class PlatesDetector:
    id: ClassVar[str] = "plates"
    lane: ClassVar[Lane] = Lane.ACCURATE
    categories: ClassVar[frozenset[Category]] = CATEGORIES
    device: ClassVar[Device] = Device.GPU_OPTIONAL  # the shared OCR sessions may run on CUDA
    # no model of its own: the OCR service is shared and its VRAM is declared (and counted) by ``cards``
    vram_mb: ClassVar[int] = 0

    def __init__(self) -> None:
        self._res: Resources | None = None
        self._cfg: DetectorConfig | None = None
        self._gate: VehicleGate | None = None
        self._refine = True
        self._shape_fallback = SHAPE_FALLBACK_DEFAULT

    # ------------------------------------------------------------------ plugin interface
    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._cfg, self._res = cfg, res
        self._shape_fallback = bool(cfg.params.get("shape_fallback", SHAPE_FALLBACK_DEFAULT))
        gate = cfg.params.get("vehicle_gate")
        self._gate = gate if callable(gate) else None
        self._refine = bool(cfg.params.get("refine_quad", True))

    def set_shape_fallback(self, on: bool) -> None:
        """Switch the plate-shape fallback (only ever active with ``mask_when_unsure`` on for plates)."""
        self._shape_fallback = bool(on)

    def warmup(self) -> None:
        warm = getattr(self._res, "warm_ocr", None)
        if warm is not None:
            warm()

    def close(self) -> None:
        self._res = None

    # ------------------------------------------------------------------ detection
    def detect(self, frame: Frame, ctx: DetectContext) -> list[Detection]:
        if self._res is None or self._cfg is None:
            raise RuntimeError("plates detector used before configure()")
        conf = self._cfg.categories.get(Category.PLATE)
        if conf is None or not conf.enabled:
            return []
        ocr = self._res.ocr(frame)  # raises when OCR is unavailable: the lane run fails (PROTECT)
        out = self._read_plates(frame, ocr)
        if self._shape_fallback and conf.mask_when_unsure and degraded(frame.image):
            late = ctx.deadline is not None and self._res.clock() > ctx.deadline
            if not late:
                out += self._shape_plates(frame, [d.quad for d in out], ocr)
        return out

    def _shape_plates(self, frame: Frame, covered: list[Quad], ocr: OcrResult) -> list[Detection]:
        """Plate-shaped rectangles with character texture that no OCR reading explains (``shape.py``)."""
        assert self._res is not None
        out: list[Detection] = []
        taken = [np.array(q.pts, np.float32) for q in covered]
        boxes = [as_array(q) for q in ocr.boxes]
        texts = [normalise_text(t).strip() for t in ocr.texts]
        confs = [float(c) for c in ocr.confs]
        for pts, layout, peaks in find_plate_shapes(frame.image):
            if any(_overlap(pts, t) > 0.3 for t in taken):
                continue
            if read_inside(pts, boxes, texts, confs) >= 1:  # a confident reading that is no plate: not unsure
                continue
            quad: Quad = to_quad(pts).clamped()
            out.append(
                Detection(
                    detector=self.id,
                    category=Category.PLATE,
                    quad=quad,
                    confidence=UNSURE_CONF,
                    frame_seq=frame.seq,
                    pts=frame.pts,
                    completed_at=self._res.clock(),
                    extra={"reason": "plate_shape_unsure", "layout": layout, "strokes": peaks},
                )
            )
        return out

    def _read_plates(self, frame: Frame, ocr: OcrResult) -> list[Detection]:
        if not ocr.boxes:
            return []
        boxes = [as_array(q) for q in ocr.boxes]
        texts = [normalise_text(t).strip() for t in ocr.texts]
        confs = [float(c) for c in ocr.confs]
        sizes = [side_lengths(b) for b in boxes]
        usable = [
            i
            for i in range(len(boxes))
            if texts[i] and _lower_bound_text(texts[i]) and sizes[i][1] >= MIN_TEXT_HEIGHT_PX
        ]
        if len(usable) > MAX_BOXES:  # keep the biggest: a plate is large text next to clutter
            usable = sorted(usable, key=lambda i: -sizes[i][0] * sizes[i][1])[:MAX_BOXES]
        if not usable:
            return []
        cands = self._candidates(boxes, texts, sizes, usable)
        accepted = self._pick(cands)
        area = float(frame.image.shape[0] * frame.image.shape[1])
        out: list[Detection] = []
        for c in accepted:
            pts_list = [boxes[i] for i in c.idx]
            plate = self._plate_quad(frame.image, pts_list, c.lines, ASPECT[c.layout])
            long_s, short_s = side_lengths(plate)
            lo_a, hi_a = ASPECT[c.layout]
            if short_s <= 0 or not lo_a <= long_s / short_s <= hi_a:
                continue
            if poly_area(plate) > MAX_PLATE_AREA * area:
                continue
            if self._gate is not None and not self._gate(frame.image, plate):
                continue
            ocr_conf = float(np.mean([confs[i] for i in c.idx]))
            score = c.match.confidence * (0.85 + 0.15 * min(1.0, ocr_conf))
            out.append(self._detection(frame, plate, score, c))
        return out

    # ------------------------------------------------------------------ candidate readings
    def _candidates(
        self,
        boxes: list[np.ndarray],
        texts: list[str],
        sizes: list[tuple[float, float]],
        usable: list[int],
    ) -> list[_Candidate]:
        out: list[_Candidate] = []

        def consider(idx: tuple[int, ...], text: str, layout: str, lines: int) -> None:
            m = match_plate(text)
            if m is not None:
                out.append(_Candidate(idx, m, layout, lines))

        for i in usable:
            consider((i,), texts[i], "one_line", 1)
        sub = [boxes[i] for i in usable]
        for group in group_lines(sub):
            members = [usable[k] for k in group]
            for size in range(2, MAX_WINDOW + 1):
                for start in range(0, len(members) - size + 1):
                    idx = tuple(members[start : start + size])
                    consider(idx, "".join(texts[i] for i in idx), "one_line", 1)
        for pair in self._stacked_pairs(boxes, sizes, usable):
            consider(pair, "".join(texts[i] for i in pair), "two_line", 2)
        return out

    @staticmethod
    def _stacked_pairs(
        boxes: list[np.ndarray], sizes: list[tuple[float, float]], usable: list[int]
    ) -> list[tuple[int, int]]:
        """Pairs (top, bottom) of text lines stacked like a two-line plate: aligned centres, similar height."""
        info = {i: box_axis(boxes[i]) for i in usable}
        centre = {i: boxes[i].mean(axis=0) for i in usable}
        pairs: list[tuple[int, int]] = []
        for a in usable:
            ua, la, ha = info[a]
            for b in usable:
                if b == a:
                    continue
                ub, lb, hb = info[b]
                if abs(float(np.dot(ua, ub))) < 0.96:  # directions within about 16 degrees
                    continue
                h = (ha + hb) / 2.0
                if not 0.6 <= ha / hb <= 1.67 or h <= 0:
                    continue
                d = centre[b] - centre[a]
                n = np.array([-ua[1], ua[0]], np.float32)
                perp = float(np.dot(d, n))
                along = abs(float(np.dot(d, ua)))
                if along > 0.45 * max(la, lb) + 0.5 * h:
                    continue
                if not STACK_GAP_H[0] * h <= abs(perp) <= STACK_GAP_H[1] * h * 1.4:
                    continue
                if centre[b][1] > centre[a][1]:  # ``a`` is the upper line
                    pairs.append((a, b))
        return pairs

    @staticmethod
    def _pick(cands: list[_Candidate]) -> list[_Candidate]:
        """One reading per set of boxes: the best score, then the one with more boxes; no box used twice."""
        ranked = sorted(cands, key=lambda c: (-c.match.confidence, -len(c.idx)))
        used: set[int] = set()
        out: list[_Candidate] = []
        for c in ranked:
            if used.intersection(c.idx):
                continue
            used.update(c.idx)
            out.append(c)
        return out

    # ------------------------------------------------------------------ plate geometry
    @staticmethod
    def _grow(text_quad: np.ndarray, lines: int) -> np.ndarray:
        """The text union grown by the plate border and the IND strip (per-line height based)."""
        u, length, height = box_axis(text_quad)
        n = np.array([-u[1], u[0]], np.float32)
        c = text_quad.mean(axis=0)
        line_h = height / lines
        half_l = length / 2.0 + PAD_ALONG * line_h
        half_h = height / 2.0 + PAD_ACROSS * line_h
        pts = np.array(
            [
                c - u * half_l - n * half_h,
                c + u * half_l - n * half_h,
                c + u * half_l + n * half_h,
                c - u * half_l + n * half_h,
            ]
        )
        return order_quad(pts.astype(np.float32))

    def _plate_quad(
        self, image: np.ndarray, parts: list[np.ndarray], lines: int, aspect: tuple[float, float]
    ) -> np.ndarray:
        text_quad = parts[0] if len(parts) == 1 else union_quad(parts)
        grown = self._grow(text_quad, lines)
        if not self._refine:
            return grown
        found = _contour_rect(image, text_quad, grown, aspect)
        if found is None:
            return grown
        return order_quad(cv2.boxPoints(cv2.minAreaRect(np.concatenate([grown, found]).astype(np.float32))))

    # ------------------------------------------------------------------ output
    def _detection(self, frame: Frame, plate: np.ndarray, score: float, c: _Candidate) -> Detection:
        assert self._res is not None
        quad: Quad = to_quad(plate).clamped()
        reason = "plate_bh" if c.match.series == "bh" else "plate_standard"
        return Detection(
            detector=self.id,
            category=Category.PLATE,
            quad=quad,
            confidence=float(min(0.99, max(0.05, score))),
            frame_seq=frame.seq,
            pts=frame.pts,
            completed_at=self._res.clock(),
            extra={
                "reason": reason,
                "series": c.match.series,
                "layout": c.layout,
                "repairs": c.match.repairs,
                "boxes": len(c.idx),
                "vehicle_gate": self._gate is not None,
            },
        )


def _contour_rect(
    image: np.ndarray, text_quad: np.ndarray, grown: np.ndarray, aspect: tuple[float, float]
) -> np.ndarray | None:
    """The plate's own rectangle around the text: the tightest contour that contains the text and the grown
    box centre, with a plate aspect and a plausible size. None when no such contour is found."""
    h_img, w_img = image.shape[:2]
    long_s, short_s = side_lengths(grown)
    margin = 0.6 * short_s
    x0 = int(max(0, grown[:, 0].min() - margin))
    y0 = int(max(0, grown[:, 1].min() - margin))
    x1 = int(min(w_img, grown[:, 0].max() + margin))
    y1 = int(min(h_img, grown[:, 1].max() + margin))
    if x1 - x0 < 16 or y1 - y0 < 12:
        return None
    roi = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    roi = cv2.GaussianBlur(roi, (3, 3), 0)
    edges = cv2.Canny(roi, 40, 120)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    offset = np.array([x0, y0], np.float32)
    text_area = max(poly_area(text_quad), 1.0)
    grown_area = poly_area(grown)
    text_pts = (text_quad - offset).astype(np.float32)
    best: tuple[float, np.ndarray] | None = None
    for cnt in contours:
        if len(cnt) < 8:
            continue
        rect = cv2.minAreaRect(cnt)
        box = cv2.boxPoints(rect).astype(np.float32)
        area = float(rect[1][0] * rect[1][1])
        if not CONTOUR_MIN_GROWTH * text_area <= area <= CONTOUR_MAX_GROWTH * grown_area:
            continue
        lo, sh = max(rect[1]), min(rect[1])
        if sh <= 0 or not aspect[0] <= lo / sh <= aspect[1]:
            continue
        contour = box.reshape(-1, 1, 2)
        if any(cv2.pointPolygonTest(contour, (float(x), float(y)), True) < -1.0 for x, y in text_pts):
            continue
        if best is None or area < best[0]:
            best = (area, box)
    if best is None:
        return None
    return order_quad(best[1] + offset)
