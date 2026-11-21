"""Shape / template fallbacks of ``screens`` (``mask_when_unsure``), for frames OCR could not read (T7.5).

* ``find_banner_shapes``: a light, notification-banner-shaped rectangle (2:1 to 9:1) with text-line texture that OCR
  did not read. Whether it holds an OTP cannot be told from the shape, so every unread banner of a phone is masked
  (a banner that OCR read confidently is never touched).
* ``relaxed_hits``: the game-room templates (``templates.py``) matched at a lower threshold than the strict route,
  confirmed by the colour of the game's UI accent (amber bars), for blurred or dark frames where the strict anchor
  score drops. Regions that hold a confident reading are skipped.

Nothing is read; detections carry a low confidence so the planner holds them only briefly.
"""

from __future__ import annotations

import dataclasses

import cv2
import numpy as np

from vision.ocr.layout import poly_area
from vision.shapes import find_rects, oriented_patch, read_inside, text_bands, work_image

from .templates import GameTemplate, TemplateHit, TemplateMatcher

CONF = 0.56
BANNER_ASPECT = (2.0, 9.0)
BANNER_MIN_AREA, BANNER_MAX_AREA_FRAC = 2200.0, 0.08
BANNER_MIN_BANDS = 2
RELAX = 0.72  # the relaxed anchor threshold, as a share of the template's own


def find_banner_shapes(
    image: np.ndarray, boxes: list[np.ndarray], texts: list[str], confs: list[float]
) -> list[tuple[np.ndarray, int]]:
    h, w = image.shape[:2]
    gray_small, s = work_image(image)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    rects = find_rects(
        gray_small,
        s,
        min_area=BANNER_MIN_AREA,
        max_area=BANNER_MAX_AREA_FRAC * h * w,
        aspect=BANNER_ASPECT,
        min_fill=0.85,
    )
    out: list[tuple[np.ndarray, int]] = []
    for r in rects:
        q = r.quad
        if (q <= 1).any() or (q[:, 0] >= w - 2).any() or (q[:, 1] >= h - 2).any():
            continue
        if read_inside(q, boxes, texts, confs) >= 1:
            continue
        patch = oriented_patch(gray, q, 192, 48)
        # a banner is a light card on a darker screen: clearly brighter than the frame (dark frames included)
        if patch is None or float(patch.mean()) < float(np.median(gray)) + 22.0:
            continue
        bands, _share = text_bands(patch)
        if bands >= BANNER_MIN_BANDS:
            out.append((q, bands))
    return out


def relaxed_matcher(templates: list[GameTemplate]) -> TemplateMatcher:
    return TemplateMatcher(
        [dataclasses.replace(t, threshold=t.threshold * RELAX) for t in templates if not t.weak]
    )


def amber_share(image: np.ndarray) -> float:
    """Share of the frame in the game UI accent colour (amber / orange-yellow, saturated, bright enough)."""
    small = cv2.resize(image, (320, int(image.shape[0] * 320 / image.shape[1])), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    m = (hsv[..., 0] >= 10) & (hsv[..., 0] <= 30) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 90)
    return float(m.mean())


def relaxed_hits(
    matcher: TemplateMatcher,
    strict: list[TemplateHit],
    image: np.ndarray,
    boxes: list[np.ndarray],
    texts: list[str],
    confs: list[float],
) -> list[TemplateHit]:
    """Anchor hits that only pass the relaxed threshold, with unread regions and the accent colour present."""
    if not matcher.templates:
        return []
    done = {h.template for h in strict}
    hits = [h for h in matcher.match(image) if h.template not in done]
    if not hits or amber_share(image) < 0.004:
        return []
    out: list[TemplateHit] = []
    for h in hits:
        regions = tuple(
            (cat, q) for cat, q in h.regions if read_inside(q, boxes, texts, confs) < 1 and poly_area(q) > 0
        )
        if regions:
            out.append(dataclasses.replace(h, regions=regions))
    return out
