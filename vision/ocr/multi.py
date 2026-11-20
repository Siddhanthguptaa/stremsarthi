"""Indic enrichment of the shared OCR pass: Devanagari, Tamil and Telugu recognisers on the leftovers.

``OcrService`` (one pass per frame, shared by cards, text, plates and screens) reads every box with the Latin
recogniser. Boxes in an Indic script come back empty or with low confidence. ``IndicEnricher`` runs AFTER that
pass, once per frame (memoised), and re-reads only those boxes with the recogniser of their script
(``ppocr-v5-{devanagari,tamil,telugu}-rec``, RapidOCR ONNX), within ``ocr.rec_max_crops`` crops and
``ocr.rec_budget_ms`` of recognition time. It returns a new ``OcrResult`` (the base result is never mutated),
with Indic digits normalised to ASCII. Boxes it cannot read (Kannada, Bengali, over budget) stay empty:
callers treat an empty box as unrecognised and mask it (signboard rule / ``mask_when_unsure``).

Wiring (no change to ``OcrService``): ``install_indic(resources)`` attaches an enricher as
``resources.ocr_indic``; detectors read OCR through ``read_ocr(resources, frame)``, which falls back to the
plain shared result when no enricher is installed (fake OCR providers in tests, a missing model).

Privacy: text lives in the returned ``OcrResult`` for one frame, never logged; timings are numbers only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from rapidocr.utils.process_img import get_rotate_crop_image

from contracts import Config, Frame, OcrResult

from ..ort import OrtSessionFactory
from .latin import LineRecogniser
from .normalize import normalise_text, script_of
from .scripts import ScriptHints, headline_strength, script_order
from .service import MIN_CROP_HEIGHT_PX, NUMBER_LIKE, OcrService, box_size, digit_likeness

log = logging.getLogger("vision.ocr")

INDIC_RECOGNISERS: dict[str, tuple[str, str]] = {  # script -> (model id, RapidOCR language)
    "devanagari": ("ppocr-v5-devanagari-rec", "devanagari"),
    "tamil": ("ppocr-v5-tamil-rec", "ta"),
    "telugu": ("ppocr-v5-telugu-rec", "te"),
}
# The Latin recogniser reads Indic glyphs as confident-looking garbage (measured 0.42-0.81 on SPECIMEN signs and
# lookalike digits for Devanagari numerals) while real Latin text scores 0.92-1.0, so anything under 0.85 is
# re-read. A wrong-script Indic reading (Kannada through the Telugu model) scored 0.79-0.81, correct ones
# 0.93-0.99, so a reading is kept only at 0.85 or more.
LOW_CONF = 0.85  # a base box with text under this confidence (or no text) is re-read
ACCEPT_CONF = 0.85  # an Indic reading under this is discarded (wrong script or not text)
BETTER_BY = 0.05  # a new reading replaces an existing low-confidence one only if this much better
BATCH = 2  # small batches so the budget is checked often
MIN_BUDGET_MS = 60.0  # the enrichment always gets at least this, even when the Latin pass spent its budget

Recogniser = Callable[[list[np.ndarray]], tuple[list[str], list[float]]]


@dataclass(frozen=True, slots=True)
class IndicTiming:
    """Numbers only (never text): what the last enrichment cost."""

    rec_ms: float = 0.0
    candidates: int = 0
    recognised: int = 0
    budget_hit: bool = False
    memo_hit: bool = False


def _needs_reread(text: str, conf: float) -> bool:
    """Re-read a box with no text or a low-confidence reading, and a confident reading of a number whose label
    may be an Indic word the Latin recogniser turned into a stray character glued to the digits."""
    if not text or conf < LOW_CONF:
        return True
    digits = sum(c.isdigit() for c in text)
    letters = sum(c.isalpha() for c in text)
    return digits >= 6 and letters <= 1  # an orphan number: a label would have left more than a stray letter


def _accepts(old_text: str, old_conf: float, raw: str, new_text: str, conf: float) -> bool:
    """Whether an Indic reading replaces the Latin one of the same box."""
    if conf < ACCEPT_CONF:
        return False
    if not old_text:
        return True
    if old_conf < LOW_CONF:
        return conf >= old_conf + BETTER_BY
    # a confident Latin reading (of a number): only a reading that keeps its digits and adds Indic letters
    d_old = sum(c.isdigit() for c in old_text)
    d_new = sum(c.isdigit() for c in new_text)
    indic_letters = sum(1 for c in raw if not c.isascii() and c.isalpha())
    return conf >= 0.9 and indic_letters >= 2 and d_new >= d_old - 1


class IndicEnricher:
    """``__call__(frame, base_result) -> OcrResult``. Thread-safe; recognisers load lazily or in ``warmup``."""

    def __init__(
        self,
        config: Config,
        factory: OrtSessionFactory | None = None,
        *,
        loader: Callable[[str], Recogniser] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        budget_ms: float | None = None,
        spent_ms: Callable[[], float] | None = None,
    ) -> None:
        if factory is None and loader is None:
            raise ValueError("IndicEnricher needs an OrtSessionFactory or a recogniser loader")
        self.config = config
        self._factory = factory
        self._loader = loader or self._load_session_recogniser
        self._clock = clock
        self._budget_ms = float(config.ocr.rec_budget_ms if budget_ms is None else budget_ms)
        self._spent_ms = spent_ms  # recognition time the Latin pass already used on this frame
        self._lock = threading.RLock()
        self._recs: dict[str, Recogniser | None] = {}
        self._hints = ScriptHints()
        self._memo_key: tuple[int, float, float] | None = None
        self._memo_base: OcrResult | None = None
        self._memo: OcrResult | None = None
        self.last_timing = IndicTiming()

    # ------------------------------------------------------------------ models
    def _load_session_recogniser(self, script: str) -> Recogniser:
        assert self._factory is not None
        model_id, lang = INDIC_RECOGNISERS[script]
        batch = BATCH * 2 if self._factory.settings.provider == "cuda" else BATCH
        return LineRecogniser(self._factory.session(model_id), lang, batch)

    def _recogniser(self, script: str) -> Recogniser | None:
        if script not in self._recs:
            try:
                self._recs[script] = self._loader(script)
            except Exception as exc:  # noqa: BLE001 - best effort: the box stays unrecognised (masked)
                log.warning("indic recogniser %s unavailable (%s)", script, type(exc).__name__)
                self._recs[script] = None
        return self._recs[script]

    def warmup(self) -> None:
        """Load the three recognisers now so the first Indic frame is not slow."""
        with self._lock:
            for script in INDIC_RECOGNISERS:
                self._recogniser(script)

    def close(self) -> None:
        with self._lock:
            self._recs.clear()
            self._memo = self._memo_base = self._memo_key = None

    # ------------------------------------------------------------------ enrichment
    def __call__(self, frame: Frame, base: OcrResult) -> OcrResult:
        key = (frame.seq, frame.pts, frame.arrival)
        with self._lock:
            if self._memo is not None and self._memo_key == key and self._memo_base is base:
                self.last_timing = IndicTiming(memo_hit=True)
                return self._memo
            result = self._enrich(frame, base)
            self._memo_key, self._memo_base, self._memo = key, base, result
            return result

    def _candidates(self, image: np.ndarray, base: OcrResult) -> tuple[list[int], dict[int, np.ndarray]]:
        boxes = [np.asarray(q.pts, np.float32) for q in base.boxes]
        eligible = []
        for i, b in enumerate(boxes):
            long_side, short_side = box_size(b)
            if short_side < MIN_CROP_HEIGHT_PX or long_side < 1.2 * short_side:
                continue
            if not _needs_reread(base.texts[i], base.confs[i]):
                continue
            eligible.append(i)
        crops = {i: get_rotate_crop_image(image, boxes[i]) for i in eligible}
        likeness = {
            i: digit_likeness(crops[i], box_size(boxes[i])[0] / max(box_size(boxes[i])[1], 1e-6))
            for i in eligible
        }
        order = sorted(
            eligible,
            key=lambda i: (likeness[i] < NUMBER_LIKE, -float(np.prod(box_size(boxes[i])))),
        )
        return order[: self.config.ocr.rec_max_crops], crops

    def _enrich(self, frame: Frame, base: OcrResult) -> OcrResult:
        t0 = self._clock()
        order, crops = self._candidates(frame.image, base)
        if not order:
            self.last_timing = IndicTiming()
            return base
        now = frame.pts
        quads = base.boxes
        bboxes = {i: quads[i].bbox() for i in order}
        tries = {
            i: script_order(headline_strength(crops[i]), self._hints.lookup(bboxes[i], now)) for i in order
        }
        best: dict[int, tuple[str, float, str]] = {}  # i -> accepted (raw text, conf, script)
        spent = self._spent_ms() if self._spent_ms is not None else 0.0
        budget_s = max(MIN_BUDGET_MS, self._budget_ms - spent) / 1000.0
        budget_hit = False
        ran_any = False
        for stage in range(len(INDIC_RECOGNISERS)):
            groups: dict[str, list[int]] = {}
            for i in order:
                if i in best:  # an accepted reading: no need to try the other scripts
                    continue
                groups.setdefault(tries[i][stage], []).append(i)
            for script, idxs in groups.items():
                rec = self._recogniser(script)
                if rec is None:
                    continue
                for start in range(0, len(idxs), BATCH):
                    if ran_any and self._clock() - t0 >= budget_s:
                        budget_hit = True
                        break
                    chunk = idxs[start : start + BATCH]
                    ran_any = True
                    texts, confs = rec([crops[i] for i in chunk])
                    for i, t, c in zip(chunk, texts, confs, strict=True):
                        norm = normalise_text(t).strip()
                        accepted = bool(norm) and _accepts(base.texts[i], base.confs[i], t, norm, c)
                        if accepted and (i not in best or c > best[i][1]):
                            best[i] = (t, c, script)
                if budget_hit:
                    break
            if budget_hit:
                break
        texts_out = list(base.texts)
        confs_out = list(base.confs)
        scripts_out = list(base.scripts)
        found: list[tuple[tuple[float, float, float, float], str]] = []
        recognised = 0
        for i, (raw, conf, script) in best.items():
            text = normalise_text(raw).strip()
            texts_out[i], confs_out[i] = text, float(min(1.0, max(0.0, conf)))
            label = script_of(raw)
            if label == "unknown" or (label == "latin" and not any(c.isascii() and c.isalpha() for c in raw)):
                label = script  # a digits-only reading by an Indic recogniser
            scripts_out[i] = label
            found.append((bboxes[i], script))
            recognised += 1
        self._hints.update(found, now)
        self.last_timing = IndicTiming((self._clock() - t0) * 1000.0, len(order), recognised, budget_hit)
        if not recognised:
            return base
        return OcrResult(quads, tuple(texts_out), tuple(confs_out), tuple(scripts_out))


# ---------------------------------------------------------------------------- wiring helpers
def install_indic(res: Any) -> IndicEnricher | None:
    """Attach an ``IndicEnricher`` to ``res`` as ``ocr_indic`` (idempotent). Returns it, or None when
    ``res`` has no real OCR service (a fake provider in tests) or no config/factory to build one."""
    existing = getattr(res, "ocr_indic", None)
    if isinstance(existing, IndicEnricher):
        return existing
    config, factory = getattr(res, "config", None), getattr(res, "factory", None)
    if config is None or factory is None or not isinstance(getattr(res, "ocr", None), OcrService):
        return None
    service = res.ocr
    enricher = IndicEnricher(config, factory, spent_ms=lambda: service.last_timing.rec_ms)
    try:
        res.ocr_indic = enricher
    except AttributeError:
        return None
    return enricher


def read_ocr(res: Any, frame: Frame) -> OcrResult:
    """The shared OCR result for ``frame``, Indic-enriched when an enricher is installed on ``res``."""
    base: OcrResult = res.ocr(frame)
    enricher = getattr(res, "ocr_indic", None)
    return enricher(frame, base) if enricher is not None else base


__all__ = ["IndicEnricher", "IndicTiming", "install_indic", "read_ocr"]
