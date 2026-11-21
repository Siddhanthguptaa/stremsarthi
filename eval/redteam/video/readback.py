"""Read-back checks on the OUTPUT frame: can a reader still recover the target after the masks were painted?

* ``qr``   zxing-cpp decodes a QR whose text is the SPECIMEN payload (full frame, then the target ROI at 2x).
* ``text`` RapidOCR on the target's ROI returns a long enough run of the expected value (after normalisation).

This is a different engine from the one the detectors use for planning (zxing is shared for QR, which is the point:
if a detector cannot decode the QR a viewer's phone can). The OCR is injectable so unit tests run without models.
The output frame is painted with the production painter (``engine.blur.paint_polygon``) and the category's
configured style; a PROTECT frame is the mean colour fill, so it can never leak.
"""

from __future__ import annotations

import difflib
import math
import unicodedata
from collections.abc import Callable, Sequence
from typing import Any

import cv2
import numpy as np

from .model import Expect

OcrFn = Callable[[np.ndarray], str]
MIN_RUN = 4
MAX_RUN = 10
RUN_FRACTION = 0.6


def norm(s: str) -> str:
    """Upper-case alphanumerics only, every Unicode decimal digit mapped to ASCII; separators and U+200B vanish."""
    out = []
    for ch in s:
        if ch.isdecimal():
            try:
                out.append(str(unicodedata.digit(ch)))
            except ValueError:
                pass
        elif ch.isalnum():
            out.append(ch.upper())
    return "".join(out)


def needed_run(n: int) -> int:
    return min(n, MAX_RUN, max(MIN_RUN, math.ceil(RUN_FRACTION * n)))


def readable(values: Sequence[str], text: str) -> bool:
    """True when OCR ``text`` contains a run of one expected value at least ``needed_run`` characters long."""
    t = norm(text)
    if not t:
        return False
    for v in values:
        nv = norm(v)
        if len(nv) < MIN_RUN:
            continue
        m = difflib.SequenceMatcher(None, nv, t, autojunk=False).find_longest_match(0, len(nv), 0, len(t))
        if m.size >= needed_run(len(nv)):
            return True
    return False


# ---------------------------------------------------------------------------------------------- painting
def paint_output(
    image: np.ndarray, masks: Sequence[Any], protect: bool, styles: dict[str, Any] | None = None
) -> np.ndarray:
    """The frame as a viewer would see it: masks painted in the category's style (blur/pixelate/solid)."""
    if protect:
        b, g, r, _ = cv2.mean(image)
        return cv2.resize(
            np.array([[(round(b), round(g), round(r))]], np.uint8),
            (image.shape[1], image.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    out = image.copy()
    try:
        from contracts import Category, MaskStyle
        from engine.blur import paint_polygon

        order = {MaskStyle.BLUR: 0, MaskStyle.PIXELATE: 1, MaskStyle.SOLID: 2}
        jobs = []
        for m in masks:
            try:
                cat = Category(m.category)
            except ValueError:
                cat = None
            style = (styles or {}).get(m.category, MaskStyle.SOLID)
            jobs.append((order[style], style, cat, np.asarray(m.quad, np.float64)))
        for _, style, cat, pts in sorted(jobs, key=lambda j: j[0]):
            paint_polygon(out, pts, style, cat)
    except ImportError:  # no engine: solid fill (strictest style, so a missing painter can only hide leaks)
        for m in masks:
            cv2.fillConvexPoly(out, np.round(np.asarray(m.quad, np.float64)).astype(np.int32), (32, 32, 32))
    return out


def styles_from_config(cfg: Any) -> dict[str, Any]:
    return {c.value: v.style for c, v in cfg.categories.items()}


# ---------------------------------------------------------------------------------------------- readers
def roi(image: np.ndarray, quad: Sequence[Sequence[float]], pad: int = 6) -> np.ndarray | None:
    arr = np.asarray(quad, dtype=np.float64)
    x1, y1 = max(0, int(arr[:, 0].min()) - pad), max(0, int(arr[:, 1].min()) - pad)
    x2, y2 = (
        min(image.shape[1], int(arr[:, 0].max()) + pad + 1),
        min(image.shape[0], int(arr[:, 1].max()) + pad + 1),
    )
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return image[y1:y2, x1:x2]


class Reader:
    """Decode/OCR helper. ``ocr`` is lazily a RapidOCR engine; tests pass a fake."""

    def __init__(self, ocr: OcrFn | None = None) -> None:
        self._ocr = ocr
        self.calls = {"ocr": 0, "qr": 0}

    def ocr(self, bgr: np.ndarray) -> str:
        if self._ocr is None:
            self._ocr = _rapid_ocr()
        self.calls["ocr"] += 1
        return self._ocr(bgr)

    def decode_qr(self, image: np.ndarray, quad: Sequence[Sequence[float]]) -> list[str]:
        import zxingcpp

        self.calls["qr"] += 1
        fmt = zxingcpp.BarcodeFormat.QRCode
        found = [r.text for r in zxingcpp.read_barcodes(image, formats=fmt)]
        if found:
            return found
        r = roi(image, quad, pad=24)
        if r is None:
            return []
        up = cv2.resize(r, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        return [x.text for x in zxingcpp.read_barcodes(up, formats=fmt)]

    def recovers(self, image: np.ndarray, quad: Sequence[Sequence[float]], expect: Expect) -> bool | None:
        """True when the target is recoverable from ``image``; None when this expectation has no read-back."""
        if expect.kind == "qr":
            got = self.decode_qr(image, quad)
            return any(v == g or v in g or g in v for g in got for v in expect.values)
        if expect.kind == "text":
            r = roi(image, quad)
            if r is None:
                return False
            if max(r.shape[:2]) < 300:
                r = cv2.resize(r, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            return readable(expect.values, self.ocr(r))
        return None


def _rapid_ocr() -> OcrFn:
    from rapidocr import RapidOCR

    eng = RapidOCR()

    def run(bgr: np.ndarray) -> str:
        res = eng(bgr)
        txts = getattr(res, "txts", None)
        return " ".join(txts) if txts else ""

    return run


def ocr_available() -> str | None:
    """None when read-back OCR can run, else the reason (reported; the run falls back to proxy only)."""
    try:
        import rapidocr  # noqa: F401
        import zxingcpp  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"read-back unavailable ({type(exc).__name__})"
    return None
