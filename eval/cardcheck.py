"""Card-visibility checker: is a SPECIMEN Aadhaar card still readable in an OUTPUT frame?

Two independent tests per frame, both anchored on the ground-truth corners the sender/scene metadata gives:

1. **Structure test (every frame, about 2 ms).** The output frame is rectified over the card's corners onto the
   unmasked reference sprite's grid and the two are compared after a high-pass filter (zero-mean normalised
   correlation). A sharp card correlates at 0.7 to 0.95; a blurred, pixelated or solid-filled card at about
   0. The whole card and the number band are scored separately; either above ``SIM_LEAK`` counts as a leak.
   (Spec 5.5 names SSIM > 0.6; plain SSIM calls a well-blurred card similar because flat areas match, so the
   structural part is compared after a high-pass.)
2. **OCR test (sampled, about 0.4 s each).** RapidOCR reads the rectified crop; a Verhoeff-valid 12-digit read
   is a leak (spec 5.5 and plan gate G1). ``exact_read`` also records whether it equals the printed number.

A frame "shows the card" when either test fires. Slate frames (PROTECT, HOLD, transport) are recognised by
``is_slate`` and never show a card. Everything here works on in-memory arrays; nothing is written to disk.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from .data_gen.common import verhoeff
from .sender.scenes import Target, sprite_array, sprite_number, sprite_number_box

SIM_LEAK = 0.30  # high-pass correlation above this: the card's structure is still recoverable
SLATE_SHARE = 0.80  # share of pixels in the dominant colour that makes a frame a slate
CANVAS = (1280, 720)

_READ_RE = re.compile(r"(?<!\d)(\d{4})[\s.\-]?(\d{4})[\s.\-]?(\d{4})(?!\d)")


# ---------------------------------------------------------------- image helpers


def highpass(gray: np.ndarray, sigma: float = 1.5) -> np.ndarray:
    g = gray.astype(np.float32)
    return g - cv2.GaussianBlur(g, (0, 0), sigma)


def hp_ncc(a_gray: np.ndarray, b_gray: np.ndarray) -> float:
    """Zero-mean normalised correlation of the high-passed images (0 when either has no detail)."""
    a, b = highpass(a_gray), highpass(b_gray)
    a = a - a.mean()
    b = b - b.mean()
    na, nb = float(np.sqrt((a * a).sum())), float(np.sqrt((b * b).sum()))
    if na < 1e-3 or nb < 1e-3:
        return 0.0
    return float((a * b).sum() / (na * nb))


def rectify(image_bgr: np.ndarray, polygon: Any, size: tuple[int, int]) -> np.ndarray:
    """Warp the quad `polygon` (4 points tl tr br bl, in canvas pixels) of `image_bgr` to `size` (w, h).

    The polygon is given on the 1280x720 canvas and scaled when the frame has another size.
    """
    h, w = image_bgr.shape[:2]
    sx, sy = w / CANVAS[0], h / CANVAS[1]
    src = np.array([(x * sx, y * sy) for x, y in polygon], dtype=np.float32)
    dw, dh = size
    dst = np.array([[0, 0], [dw, 0], [dw, dh], [0, dh]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        image_bgr, m, (dw, dh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def is_slate(image_bgr: np.ndarray, share: float = SLATE_SHARE) -> bool:
    """A privacy slate or mean-colour fill: one (coarsely quantised) colour covers most of the frame.

    Real scene frames are textured (the SPECIMEN backdrop is a noisy gradient), so this does not fire on
    them. The stamp corner is under 1.5 % of the area and is ignored.
    """
    small = cv2.resize(image_bgr, (160, 90), interpolation=cv2.INTER_AREA)
    q = (small >> 4).astype(np.int32)
    packed = (q[..., 0] << 8) | (q[..., 1] << 4) | q[..., 2]
    counts = np.bincount(packed.ravel(), minlength=4096)
    # adjacent quantisation bins of the same colour are merged by also counting the best pair of neighbours
    best = int(counts.max())
    top = int(counts.argmax())
    for delta in (-256, 256, -16, 16, -1, 1):
        j = top + delta
        if 0 <= j < 4096:
            best = max(best, int(counts[top] + counts[j]))
    return best / packed.size >= share


# ---------------------------------------------------------------- reads


def aadhaar_reads(texts: list[str]) -> list[str]:
    """12-digit candidates (``XXXX XXXX XXXX`` with optional separators) found in OCR text lines."""
    found: list[str] = []
    for blob in [*texts, " ".join(texts)]:
        for m in _READ_RE.finditer(blob):
            digits = "".join(m.groups())
            if digits not in found:
                found.append(digits)
    return found


def read_verdict(texts: list[str], expected: str | None) -> tuple[bool, bool]:
    """(Verhoeff-valid 12-digit read present, that read equals the printed number)."""
    reads = aadhaar_reads(texts)
    valid = [r for r in reads if verhoeff.is_valid(r)]
    want = expected.replace(" ", "") if expected else None
    return bool(valid), bool(want and want in valid)


class OcrReader:
    """RapidOCR through the venv. Lazy, thread-safe; ``read`` returns the recognised text lines."""

    def __init__(self, threads: int = 2, limit_side: int = 480) -> None:
        self._threads = threads
        self._limit = limit_side
        self._engine: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR(
                params={
                    # a masked card gives "text detection result is empty" on every sampled frame (the pass case)
                    "Global.log_level": "error",
                    "Global.use_cls": False,
                    "Det.limit_side_len": self._limit,
                    "Det.limit_type": "max",
                    "EngineConfig.onnxruntime.intra_op_num_threads": self._threads,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                }
            )
        return self._engine

    def read(self, image_bgr: np.ndarray) -> list[str]:
        with self._lock:
            out = self._load()(image_bgr)
        txts = getattr(out, "txts", None)
        return [str(t) for t in txts] if txts else []


def ocr_available() -> bool:
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class CardVerdict:
    similarity: float  # high-pass correlation over the whole card
    number_similarity: float  # ... over the number band
    similar: bool  # either similarity at or above SIM_LEAK
    ocr_run: bool
    valid_read: bool | None  # None when OCR was not run
    exact_read: bool | None
    leaked: bool  # the card is recoverable: similar or a valid read

    def as_dict(self) -> dict[str, Any]:
        return {
            "similarity": round(self.similarity, 4),
            "number_similarity": round(self.number_similarity, 4),
            "similar": self.similar,
            "ocr_run": self.ocr_run,
            "valid_read": self.valid_read,
            "exact_read": self.exact_read,
            "leaked": self.leaked,
        }


class CardChecker:
    """Scores one output frame against one ground-truth Aadhaar target."""

    def __init__(self, ocr: OcrReader | None = None, sim_threshold: float = SIM_LEAK) -> None:
        self.ocr = ocr
        self.sim_threshold = sim_threshold
        self._refs: dict[
            str, tuple[np.ndarray, np.ndarray, tuple[int, int, int, int] | None, str | None]
        ] = {}

    def _ref(self, key: str) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int] | None, str | None]:
        if key not in self._refs:
            bgr = cv2.cvtColor(sprite_array(key), cv2.COLOR_RGB2BGR)
            self._refs[key] = (
                bgr,
                cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY),
                sprite_number_box(key),
                sprite_number(key),
            )
        return self._refs[key]

    def similarity(self, out_bgr: np.ndarray, target: Target) -> tuple[float, float, np.ndarray]:
        """(whole-card correlation, number-band correlation, the rectified crop)."""
        ref_bgr, ref_gray, box, _ = self._ref(target.ref or "aadhaar")
        h, w = ref_gray.shape
        crop = rectify(out_bgr, target.polygon(), (w, h))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        whole = hp_ncc(gray, ref_gray)
        band = whole
        if box is not None:
            x1, y1, x2, y2 = box
            band = hp_ncc(gray[y1:y2, x1:x2], ref_gray[y1:y2, x1:x2])
        return whole, band, crop

    def check(self, out_bgr: np.ndarray, target: Target, run_ocr: bool = False) -> CardVerdict:
        whole, band, crop = self.similarity(out_bgr, target)
        similar = max(whole, band) >= self.sim_threshold
        valid: bool | None = None
        exact: bool | None = None
        ran = False
        if run_ocr and self.ocr is not None:
            ran = True
            valid, exact = read_verdict(self.ocr.read(crop), self._ref(target.ref or "aadhaar")[3])
        return CardVerdict(whole, band, similar, ran, valid, exact, similar or bool(valid))

    def ocr_only(self, crop_bgr: np.ndarray, target: Target) -> tuple[bool, bool]:
        """OCR a crop already rectified by ``similarity`` (used by the tap's OCR worker)."""
        if self.ocr is None:
            return False, False
        return read_verdict(self.ocr.read(crop_bgr), self._ref(target.ref or "aadhaar")[3])
