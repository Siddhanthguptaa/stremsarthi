"""The shared OCR service (``contracts.OcrProvider``): one memoised pass per frame.

Flow per frame (spec 5.2): PP-OCR text detector on the whole frame at the static graph size, then the
Latin recogniser on a ranked, budgeted set of crops. Crops are ranked so number-like boxes (long runs of
uniform glyphs: Aadhaar, PAN, phone, PIN) are recognised first, then by area; recognition stops at
``ocr.rec_max_crops`` crops or ``ocr.rec_budget_ms`` (checked between small batches). Boxes that are
detected but not recognised come back with empty text and the detector score as their confidence, so a
consumer that only needs geometry (signboards) still has every box.

Privacy: the text lives in the returned ``OcrResult`` for one frame. Nothing here logs or stores it,
and exception messages are never propagated with text.

GPU: the ORT sessions come from ``OrtSessionFactory`` (CPU default; CUDA only when ``Config.gpu``
enables it, with the arena capped). Run any GPU process under ``flock /tmp/streamsarthi-gpu.lock``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from rapidocr.utils.process_img import get_rotate_crop_image

from contracts import Config, Frame, OcrResult, Quad

from ..ort import OrtSessionFactory
from .det import ALT_DET_MODEL, DEFAULT_DET_MODEL, DetParams, TextBoxDetector, static_shape
from .latin import LATIN_REC_MODEL, LineRecogniser
from .normalize import normalise_text, script_of

log = logging.getLogger("vision.ocr")

MIN_CROP_HEIGHT_PX = 10
REC_BATCH = 2  # CPU: small batches so the budget is checked often
REC_BATCH_CUDA = 8  # GPU: one launch for all crops (the budget is not the limit there)
NUMBER_LIKE = 0.6  # digit_likeness at or above this = recognised first


class OcrUnavailableError(RuntimeError):
    """The OCR models are not available (weights missing or the session failed to start)."""


@dataclass(frozen=True, slots=True)
class OcrTiming:
    """Numbers only (never text): what the last pass cost."""

    det_ms: float = 0.0
    rec_ms: float = 0.0
    boxes: int = 0
    recognised: int = 0
    memo_hit: bool = False


def box_size(box: np.ndarray) -> tuple[float, float]:
    """(long side, short side) of a detector quad (4x2), a rotation-safe width and height."""
    w = float(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3])))
    h = float(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2])))
    return (w, h) if w >= h else (h, w)


def digit_likeness(crop: np.ndarray, aspect: float) -> float:
    """Cheap 0..1 score for 'a run of 6-20 uniform-height glyphs' (a number or an ID), no recognition.

    Binarises the crop (Otsu, foreground = the minority polarity), counts connected components of glyph
    size and rewards a plausible count with uniform heights, plus a plausible aspect (long and thin).
    """
    if crop.size == 0 or crop.shape[0] < 6:
        return 0.0
    scale = 32.0 / crop.shape[0]
    gray = cv2.cvtColor(
        cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY
    )
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if binary.mean() > 127:  # foreground should be the minority class
        binary = 255 - binary
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    h_img = gray.shape[0]
    heights = [
        int(s[cv2.CC_STAT_HEIGHT])
        for s in stats[1:]
        if s[cv2.CC_STAT_AREA] >= 6 and s[cv2.CC_STAT_HEIGHT] >= 0.25 * h_img
    ]
    count_ok = 1.0 if 6 <= len(heights) <= 20 else (0.5 if 4 <= len(heights) < 6 else 0.0)
    if len(heights) >= 3:
        med = float(np.median(heights))
        uniform = sum(1 for h in heights if 0.8 * med <= h <= 1.2 * med) / len(heights)
    else:
        uniform = 0.0
    aspect_ok = 1.0 if 2.5 <= aspect <= 16.0 else (0.5 if 1.8 <= aspect < 2.5 else 0.0)
    return float(0.4 * count_ok + 0.4 * uniform + 0.2 * aspect_ok)


def rank_boxes(boxes: np.ndarray, likeness: list[float]) -> list[int]:
    """Indices in recognition order: number-like crops first, then by area, both descending."""
    areas = [float(np.prod(box_size(b))) for b in boxes]
    return sorted(range(len(boxes)), key=lambda i: (likeness[i] < NUMBER_LIKE, -areas[i]))


class OcrService:
    """Implements ``contracts.OcrProvider``. Thread-safe; sessions load lazily on first use."""

    def __init__(
        self,
        config: Config,
        factory: OrtSessionFactory,
        *,
        det_model: str = DEFAULT_DET_MODEL,
        rec_model: str = LATIN_REC_MODEL,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if det_model not in (DEFAULT_DET_MODEL, ALT_DET_MODEL, "ppocr-v6-small"):
            raise ValueError(f"unknown OCR detector {det_model!r}")
        self.config = config
        self._factory = factory
        self._det_model = det_model
        self._rec_model = rec_model
        self._clock = clock
        self._lock = threading.RLock()
        self._det: TextBoxDetector | None = None
        self._rec: LineRecogniser | None = None
        self._batch = REC_BATCH
        self._memo_key: tuple[int, float, float] | None = None
        self._memo: OcrResult | None = None
        self.last_timing = OcrTiming()

    # ------------------------------------------------------------------ lifecycle
    def warmup(self) -> None:
        """Load the sessions and run one empty frame so the first real frame is not slow."""
        with self._lock:
            self._ensure_loaded()
            assert self._det is not None
            self._det(np.zeros((720, 1280, 3), np.uint8))

    def _ensure_loaded(self) -> None:
        if self._det is not None and self._rec is not None:
            return
        side = self.config.detect.accurate_width
        height, width = static_shape(side)
        try:
            det_sess = self._factory.session(f"{self._det_model}:{height}x{width}")
            rec_sess = self._factory.session(self._rec_model)
            self._det = TextBoxDetector(det_sess, self._det_model, max(height, width), DetParams())
            self._batch = REC_BATCH_CUDA if self._factory.settings.provider == "cuda" else REC_BATCH
            self._rec = LineRecogniser(rec_sess, "en", self._batch)
        except Exception as exc:  # noqa: BLE001 - fail safe: callers treat this as a failed lane run
            raise OcrUnavailableError(f"OCR models unavailable ({type(exc).__name__})") from exc

    def close(self) -> None:
        with self._lock:
            self._det = self._rec = None
            self._memo = self._memo_key = None

    # ------------------------------------------------------------------ OcrProvider
    def __call__(self, frame: Frame) -> OcrResult:
        key = (frame.seq, frame.pts, frame.arrival)
        with self._lock:
            if self._memo is not None and self._memo_key == key:
                self.last_timing = OcrTiming(memo_hit=True, boxes=len(self._memo.boxes))
                return self._memo
            result = self._run(frame.image)
            self._memo_key, self._memo = key, result
            return result

    def _run(self, image: np.ndarray) -> OcrResult:
        self._ensure_loaded()
        assert self._det is not None and self._rec is not None
        t0 = self._clock()
        boxes, scores = self._det(image)
        t1 = self._clock()
        n = len(boxes)
        texts = [""] * n
        confs = [float(s) for s in scores]
        scripts = ["unknown"] * n
        recognised = 0
        if n:
            sizes = [box_size(b) for b in boxes]
            eligible = [i for i in range(n) if sizes[i][1] >= MIN_CROP_HEIGHT_PX]
            crops: dict[int, np.ndarray] = {}
            likeness = [0.0] * n
            for i in eligible:
                crops[i] = get_rotate_crop_image(image, boxes[i])
                likeness[i] = digit_likeness(crops[i], sizes[i][0] / max(sizes[i][1], 1e-6))
            order = [i for i in rank_boxes(boxes, likeness) if i in crops][: self.config.ocr.rec_max_crops]
            budget_s = self.config.ocr.rec_budget_ms / 1000.0
            last_batch_s = 0.0
            for start in range(0, len(order), self._batch):
                spent = self._clock() - t1
                if start and spent + last_batch_s > budget_s:
                    break  # the first batch always runs: the number-like crops are in it
                idx = order[start : start + self._batch]
                tb = self._clock()
                out_t, out_c = self._rec([crops[i] for i in idx])
                last_batch_s = self._clock() - tb
                for i, t, c in zip(idx, out_t, out_c, strict=True):
                    t = normalise_text(t).strip()
                    if t:
                        texts[i], confs[i], scripts[i] = t, c, script_of(t)
                        recognised += 1
        t2 = self._clock()
        self.last_timing = OcrTiming((t1 - t0) * 1000.0, (t2 - t1) * 1000.0, n, recognised)
        quads = tuple(
            Quad(tuple((float(x), float(y)) for x, y in b)).clamped()  # type: ignore[arg-type]
            for b in boxes
        )
        return OcrResult(quads, tuple(texts), tuple(min(1.0, max(0.0, c)) for c in confs), tuple(scripts))


def build_ocr(config: Config, factory: OrtSessionFactory) -> OcrService:
    return OcrService(config, factory)


__all__: list[Any] = ["OcrService", "OcrTiming", "OcrUnavailableError", "build_ocr"]
