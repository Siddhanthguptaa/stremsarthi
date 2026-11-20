"""Cheap script routing for Indic text crops: which recogniser should read this crop first.

PP-OCR ships recognisers for Devanagari, Tamil and Telugu (Kannada and Bengali have none on the real-time
path, spec 5.2 / D-9), and a recogniser run costs tens of milliseconds per crop on CPU, so each crop is
sent to the most likely one first instead of to all three:

* ``headline_strength`` finds a shirorekha, the continuous horizontal headline of Devanagari (and Bengali).
  A strong headline sends the crop to the Devanagari recogniser (Bengali has no recogniser, it comes back
  with low confidence and is dropped, so the box stays "unrecognised" and the signboard rule masks it).
* No headline: Tamil, Telugu (and Kannada) are all round-glyph scripts, so the crop is tried on Tamil then
  Telugu then Devanagari (numerals have no headline), the order refined by ``ScriptHints``, a tiny per-box
  memory of which script worked on the previous frame at the same place.

Everything here works on pixels of one crop and on numbers; no text is produced or stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

REC_SCRIPTS: tuple[str, ...] = ("devanagari", "tamil", "telugu")  # labels of OcrResult.scripts
HEADLINE_MIN = 0.62  # row-ink share of the text span that counts as a headline
HINT_TTL_S = 2.0
HINT_IOU = 0.4


def headline_strength(crop_bgr: np.ndarray) -> float:
    """0..1 strength of a shirorekha in a text-line crop (about 0.8+ for Devanagari/Bengali, under 0.5 else)."""
    if crop_bgr.size == 0 or crop_bgr.shape[0] < 8 or crop_bgr.shape[1] < 8:
        return 0.0
    scale = 40.0 / crop_bgr.shape[0]
    gray = cv2.cvtColor(
        cv2.resize(crop_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY
    )
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.mean() > 127:  # ink is the minority polarity
        binary = 255 - binary
    ink = binary > 0
    cols = np.flatnonzero(ink.any(axis=0))
    rows = np.flatnonzero(ink.any(axis=1))
    if cols.size < 8 or rows.size < 6:
        return 0.0
    span = float(cols[-1] - cols[0] + 1)
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    body = ink[top:bottom, cols[0] : cols[-1] + 1]
    share = body.sum(axis=1) / span
    upper = share[: max(2, int(0.6 * body.shape[0]))]
    peak = float(upper.max()) if upper.size else 0.0
    # a headline is a strong peak well above the typical row (letters give a flat profile)
    typical = float(np.median(share[share > 0])) if (share > 0).any() else 1.0
    contrast = peak / max(typical, 1e-6)
    return float(np.clip(0.7 * peak + 0.3 * min(1.0, (contrast - 1.0) / 1.5), 0.0, 1.0))


def script_order(headline: float, hint: str | None) -> list[str]:
    """Recogniser scripts to try for a crop, most likely first (a prior hint goes in front)."""
    base = (
        ["devanagari", "tamil", "telugu"] if headline >= HEADLINE_MIN else ["tamil", "telugu", "devanagari"]
    )
    if hint in base:
        base.remove(hint)
        base.insert(0, hint)
    return base


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass(slots=True)
class ScriptHints:
    """Which script read a box last time, keyed by position (bounding box IoU), valid for a short while."""

    entries: list[tuple[tuple[float, float, float, float], str, float]] = field(default_factory=list)

    def lookup(self, bbox: tuple[float, float, float, float], now: float) -> str | None:
        best, script = HINT_IOU, None
        for old, name, t in self.entries:
            if now - t <= HINT_TTL_S:
                iou = _iou(bbox, old)
                if iou >= best:
                    best, script = iou, name
        return script

    def update(self, found: list[tuple[tuple[float, float, float, float], str]], now: float) -> None:
        keep = [e for e in self.entries if now - e[2] <= HINT_TTL_S]
        keep = [e for e in keep if all(_iou(e[0], f[0]) < HINT_IOU for f in found)]
        self.entries = keep + [(bbox, name, now) for bbox, name in found]
