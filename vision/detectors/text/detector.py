"""``text`` detector: phone numbers, PIN codes, UPI IDs, e-mails, signboards and risky text (ACCURATE lane).

Flow per frame (spec 5.9, plan D-9): the shared OCR pass (``res.ocr``), then the Indic enrichment of its
empty or low-confidence boxes (Devanagari, Tamil, Telugu recognisers, ``vision.ocr.multi``). Indic digits are
already ASCII in the result. Then:

1. Structured PII on every text box, and on windows of two or three boxes of one line (a number split over
   boxes): phone (Indian mobile, ``+91``/``0`` prefixes, separators), PIN (6 digits, valid postal circle;
   needs a cue word in the same or a nearby box, or a short box at sensitivity >= 0.5), UPI ID (``name@bank``,
   not an e-mail) and e-mail. The detection quad is the text box (the planner pads it).
2. ``SIGNBOARD``: detection-only mask-all-text. Every box at least ``signboard.min_text_height_px`` tall
   with a detector confidence of at least 0.5, shaped like a text line (a big near-square region is a photo or
   UI tile, not text: ``_text_like``) (no recognition, no script assumption, so Kannada and Bengali
   boards are covered although they have no recogniser) unless an enabled PII category already masks it.
3. ``RISKY_TEXT``: only with ``guard.risky_text_blocklist`` set and the category enabled; matched in
   process on normalised text. Never logged.
4. OCR budget exhausted: an unrecognised box that looks like a digit run (``digit_likeness``) is masked as a
   phone number (else PIN) when that category has ``mask_when_unsure``, unless signboard already covers it.

Detections carry codes and numbers only (``extra`` = reason and script codes). Tracking and hold are the
planner's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
from rapidocr.utils.process_img import get_rotate_crop_image

from contracts import (
    Category,
    DetectContext,
    Detection,
    DetectorConfig,
    Device,
    Frame,
    Lane,
    Quad,
    Resources,
)
from vision.ocr.layout import as_array, group_lines, to_quad, union_quad
from vision.ocr.multi import install_indic, read_ocr
from vision.ocr.normalize import digit_ratio, normalise_text, repair_digit_field
from vision.ocr.service import MIN_CROP_HEIGHT_PX, NUMBER_LIKE, box_size, digit_likeness
from vision.patterns import find_emails, find_phones, find_pins, find_upi_ids, has_pin_cue
from vision.patterns.pin import CONF_BARE, CONF_CUED

log = logging.getLogger("vision.text")

CATEGORIES = frozenset(
    {
        Category.PHONE_NUMBER,
        Category.PIN_CODE,
        Category.UPI_ID,
        Category.EMAIL,
        Category.SIGNBOARD,
        Category.RISKY_TEXT,
    }
)
STRUCTURED = (Category.PHONE_NUMBER, Category.PIN_CODE, Category.UPI_ID, Category.EMAIL)
SHORT_BOX_CHARS = 40  # a bare PIN is accepted only in a box this short (spec 5.9)
DET_FLOOR = 0.5  # signboard: detector confidence floor for unrecognised boxes
WEAK_FLOOR = 0.3  # signboard: floor for the faint boxes of the sensitive read (``OcrService.weak_boxes``)
SIGN_MAX_AREA = 0.4  # a box larger than this share of the frame is not a text line
# Near-square regions are photos and UI tiles, not text lines (measured on the Gate 2 stills: 448 boxes on real
# SPECIMEN signboards have an aspect of 1.33 or more; the near-square ones sat on photos and faces). A large
# near-square box would blank an allowlisted face, so it is not masked as a signboard.
SQUARE_ASPECT = 1.25  # long side / short side below this ...
SQUARE_MIN_AREA = 10_000  # ... and at least this many px^2 (about 100x100): not a text line
BIG_ASPECT, BIG_MIN_AREA = 2.0, 150_000  # a very large block that is not a long line either (>= 16 % of 720p)
WEAK_MIN_ASPECT = 1.3  # a faint (weak) box must at least look like a line
CONF_RISKY = 0.7
CONF_UNSURE = 0.55
UNREADABLE_BELOW = 0.6  # a reading under this confidence is treated as no reading (digits are not trusted)
MAX_UNSURE = 6
NEIGHBOUR_V, NEIGHBOUR_H = 1.6, 3.0  # a "nearby" box: within this many text heights (vertical, horizontal)


@dataclass(slots=True)
class _Match:
    category: Category
    start: int
    end: int
    conf: float
    reason: str


def _lower_terms(terms: object) -> tuple[str, ...]:
    out: list[str] = []
    for t in terms or ():  # type: ignore[attr-defined]
        norm = normalise_text(str(t)).lower().strip()
        if norm and norm not in out:
            out.append(norm)
    return tuple(out)


class TextDetector:
    id: ClassVar[str] = "text"
    lane: ClassVar[Lane] = Lane.ACCURATE
    categories: ClassVar[frozenset[Category]] = CATEGORIES
    device: ClassVar[Device] = Device.GPU_OPTIONAL  # the shared OCR sessions may run on CUDA
    # no model of its own: the OCR service is shared and its VRAM is declared (and counted) by ``cards``
    vram_mb: ClassVar[int] = 0

    def __init__(self) -> None:
        self._res: Resources | None = None
        self._cfg: DetectorConfig | None = None
        self._min_height = 14
        self._blocklist: tuple[str, ...] = ()

    # ------------------------------------------------------------------ plugin interface
    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._cfg, self._res = cfg, res
        config = getattr(res, "config", None)
        params = cfg.params
        self._min_height = int(
            params.get(
                "signboard_min_text_height_px",
                getattr(getattr(config, "signboard", None), "min_text_height_px", 14),
            )
        )
        terms = params.get(
            "risky_text_blocklist", getattr(getattr(config, "guard", None), "risky_text_blocklist", ())
        )
        self._blocklist = _lower_terms(terms)
        install_indic(res)  # no-op for a fake OCR provider; idempotent

    def warmup(self) -> None:
        warm = getattr(self._res, "warm_ocr", None)
        if warm is not None:
            warm()
        enricher = getattr(self._res, "ocr_indic", None)
        if enricher is not None:
            enricher.warmup()

    def close(self) -> None:
        self._res = None

    # ------------------------------------------------------------------ helpers
    def _enabled(self, category: Category) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(category)
        return bool(conf is not None and conf.enabled)

    def _unsure(self, category: Category) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(category)
        return bool(conf is not None and conf.enabled and conf.mask_when_unsure)

    @staticmethod
    def _text_like(
        long_side: float, short_side: float, *, weak: bool = False, recognised: bool = False
    ) -> bool:
        """Geometry test for mask-all-text: a text line is wider than tall; a big square-ish region is a picture.

        ``recognised`` = the OCR read text in the box with confidence >= ``DET_FLOOR``: a big stacked two-line sign
        or a large short word is then real text and the near-square skip does not apply (a photo that the
        detector boxed has no confidently read text)."""
        aspect = long_side / max(short_side, 1e-6)
        area = long_side * short_side
        if not recognised:
            if aspect < SQUARE_ASPECT and area > SQUARE_MIN_AREA:
                return False
            if aspect < BIG_ASPECT and area > BIG_MIN_AREA:
                return False
        return not (weak and aspect < WEAK_MIN_ASPECT)

    def _allow_bare_pin(self) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(Category.PIN_CODE)
        return bool(conf is not None and conf.sensitivity >= 0.5)

    # ------------------------------------------------------------------ detection
    def detect(self, frame: Frame, ctx: DetectContext) -> list[Detection]:
        if self._res is None or self._cfg is None:
            raise RuntimeError("text detector used before configure()")
        want = {c for c in CATEGORIES if self._enabled(c)}
        if not self._blocklist:
            want.discard(Category.RISKY_TEXT)
        if not want:
            return []
        ocr = read_ocr(self._res, frame)  # raises when OCR is unavailable: the lane run fails (PROTECT)
        n = len(ocr.boxes)
        if n == 0 and Category.SIGNBOARD not in want:
            return []
        boxes = [as_array(q) for q in ocr.boxes]
        texts = [normalise_text(t).strip() for t in ocr.texts]
        confs = list(ocr.confs)
        sizes = [box_size(b) for b in boxes]
        out: dict[tuple[Category, tuple[int, ...]], tuple[float, np.ndarray, str, str]] = {}

        def add(category: Category, idx: tuple[int, ...], pts: np.ndarray, conf: float, reason: str) -> None:
            script = ocr.scripts[idx[0]] if idx[0] < n else "unknown"
            old = out.get((category, idx))
            if old is None or conf > old[0]:
                out[(category, idx)] = (conf, pts, reason, script)

        structured = {c for c in want if c in STRUCTURED or c is Category.RISKY_TEXT}
        if structured and n:
            self._scan_boxes(boxes, texts, confs, sizes, structured, add)
        pii_boxes = {i for (cat, idx) in out if cat in STRUCTURED for i in idx}

        sign_idx = set()
        if Category.SIGNBOARD in want:
            area = float(frame.image.shape[0] * frame.image.shape[1])
            for i in range(n):
                long_side, short_side = sizes[i]
                if short_side < self._min_height or long_side < 0.8 * short_side:
                    continue
                if long_side * short_side > SIGN_MAX_AREA * area:
                    continue
                if not self._text_like(
                    long_side, short_side, recognised=bool(texts[i]) and confs[i] >= DET_FLOOR
                ):
                    continue
                if not texts[i] and confs[i] < DET_FLOOR:
                    continue
                if i in pii_boxes:
                    continue  # an enabled PII category already masks this box
                conf = 0.8 if texts[i] else 0.55 + 0.35 * min(1.0, confs[i])
                add(Category.SIGNBOARD, (i,), boxes[i], conf, "mask_all_text")
                sign_idx.add(i)

            self._weak_signboards(frame, add)

        unsure_cat = next((c for c in (Category.PHONE_NUMBER, Category.PIN_CODE) if self._unsure(c)), None)
        if unsure_cat is not None:
            self._unrecognised_numbers(
                frame, boxes, texts, confs, sizes, sign_idx | pii_boxes, unsure_cat, add
            )

        detections = []
        for (category, idx), (conf, pts, reason, script) in out.items():
            ocr_conf = float(np.mean([confs[i] for i in idx if i < n] or [0.5]))
            if category in STRUCTURED and idx[0] < n and texts[idx[0]]:
                conf *= 0.85 + 0.15 * ocr_conf
            detections.append(self._detection(frame, category, pts, conf, reason, script, len(idx)))
        return detections

    def _weak_signboards(self, frame: Frame, add) -> None:  # noqa: ANN001
        """Mask-all-text also covers faint or blurred text the normal read dropped (geometry only)."""
        weak_fn = getattr(getattr(self._res, "ocr", None), "weak_boxes", None)
        if weak_fn is None:
            return
        wb, ws = weak_fn(frame)
        area = float(frame.image.shape[0] * frame.image.shape[1])
        base = 100000  # indices of weak boxes never collide with the OCR boxes'
        for j in range(len(wb)):
            long_side, short_side = box_size(wb[j])
            if short_side < self._min_height or long_side < 0.8 * short_side or ws[j] < WEAK_FLOOR:
                continue
            if long_side * short_side > SIGN_MAX_AREA * area:
                continue
            if not self._text_like(long_side, short_side, weak=True):
                continue
            add(Category.SIGNBOARD, (base + j,), wb[j], 0.55 + 0.35 * float(ws[j]), "mask_all_text_weak")

    # ------------------------------------------------------------------ structured scans
    def _scan_boxes(self, boxes, texts, confs, sizes, want, add) -> None:  # noqa: ANN001
        n = len(boxes)
        near = self._neighbours(boxes, sizes) if Category.PIN_CODE in want else [[] for _ in range(n)]
        short_ok = self._allow_bare_pin()
        for i, text in enumerate(texts):
            if not text:
                continue
            cue = has_pin_cue(text) or any(has_pin_cue(texts[j]) for j in near[i] if texts[j])
            # a bare 6-digit number is trusted only when the OCR is: low-confidence digits are often hallucinated
            bare = short_ok and len(text) <= SHORT_BOX_CHARS and confs[i] >= UNREADABLE_BELOW
            for m in self._scan(text, want, cue=cue, short=bare):
                add(m.category, (i,), boxes[i], m.conf, m.reason)
        # a number or address split over two or three boxes of one line
        for group in group_lines(boxes):
            for size in (2, 3):
                for start in range(0, len(group) - size + 1):
                    idx = tuple(group[start : start + size])
                    if any(not texts[i] for i in idx):
                        continue
                    joined = " ".join(texts[i] for i in idx)
                    spans, pos = [], 0
                    for i in idx:
                        spans.append((pos, pos + len(texts[i])))
                        pos += len(texts[i]) + 1
                    cue = has_pin_cue(joined)
                    bare = (
                        short_ok
                        and len(joined) <= SHORT_BOX_CHARS
                        and min(confs[i] for i in idx) >= UNREADABLE_BELOW
                    )
                    for m in self._scan(joined, want, cue=cue, short=bare):
                        hit = tuple(
                            i for i, (a, b) in zip(idx, spans, strict=True) if m.start < b and m.end > a
                        )
                        if len(hit) >= 2:
                            add(m.category, hit, union_quad([boxes[i] for i in hit]), m.conf, m.reason)

    def _scan(self, text: str, want: set[Category], *, cue: bool, short: bool) -> list[_Match]:
        out: list[_Match] = []
        if Category.PHONE_NUMBER in want:
            hits = find_phones(text)
            if not hits and digit_ratio(text) >= 0.75 and len(text) >= 9:
                fixed = repair_digit_field(text)  # O/l/B/S confusions, only in a digit-expected field
                hits = find_phones(fixed) if fixed != text else []
            for h in hits:
                out.append(_Match(Category.PHONE_NUMBER, h.start, h.end, h.confidence, "phone_pattern"))
        if Category.PIN_CODE in want:
            phones = [(m.start, m.end) for m in out]
            for h in find_pins(text):
                if any(h.start < b and h.end > a for a, b in phones):
                    continue
                if cue:
                    out.append(_Match(Category.PIN_CODE, h.start, h.end, CONF_CUED, "pin_cue"))
                elif short:
                    out.append(_Match(Category.PIN_CODE, h.start, h.end, CONF_BARE, "pin_bare"))
        if Category.UPI_ID in want:
            for u in find_upi_ids(text):
                out.append(
                    _Match(
                        Category.UPI_ID,
                        u.start,
                        u.end,
                        u.confidence,
                        "upi_known" if u.known else "upi_pattern",
                    )
                )
        if Category.EMAIL in want:
            for e in find_emails(text):
                out.append(_Match(Category.EMAIL, e.start, e.end, e.confidence, "email_pattern"))
        ids = [(m.start, m.end) for m in out if m.category in (Category.UPI_ID, Category.EMAIL)]
        if ids:  # digits inside an ID (``9876543210@ybl``) are part of the ID, not a phone number or PIN
            out = [
                m
                for m in out
                if m.category not in (Category.PHONE_NUMBER, Category.PIN_CODE)
                or not any(m.start < b and m.end > a for a, b in ids)
            ]
        if Category.RISKY_TEXT in want and self._blocklist:
            low = text.lower()
            for term in self._blocklist:
                at = low.find(term)
                if at >= 0:
                    out.append(_Match(Category.RISKY_TEXT, at, at + len(term), CONF_RISKY, "blocklist"))
        return out

    @staticmethod
    def _neighbours(boxes: list[np.ndarray], sizes: list[tuple[float, float]]) -> list[list[int]]:
        """Per box, the other boxes within a few text heights (a PIN cue may sit in the box beside or above)."""
        rects = np.array([[b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()] for b in boxes])
        out: list[list[int]] = []
        for i in range(len(boxes)):
            h = max(sizes[i][1], 8.0)
            x1, y1, x2, y2 = rects[i]
            ex1, ey1, ex2, ey2 = (
                x1 - NEIGHBOUR_H * h,
                y1 - NEIGHBOUR_V * h,
                x2 + NEIGHBOUR_H * h,
                y2 + NEIGHBOUR_V * h,
            )
            near = [
                j
                for j in range(len(boxes))
                if j != i
                and rects[j][0] < ex2
                and rects[j][2] > ex1
                and rects[j][1] < ey2
                and rects[j][3] > ey1
            ]
            out.append(near)
        return out

    # ------------------------------------------------------------------ OCR budget exhausted
    def _unrecognised_numbers(self, frame, boxes, texts, confs, sizes, skip, category, add) -> None:  # noqa: ANN001
        """Mask digit-run-looking boxes the OCR could not read (budget spent) as ``category``."""
        found = 0
        for i, b in enumerate(boxes):
            if (texts[i] and confs[i] >= UNREADABLE_BELOW) or i in skip:
                continue
            long_side, short_side = sizes[i]
            if short_side < MIN_CROP_HEIGHT_PX or not 2.0 <= long_side / max(short_side, 1e-6) <= 16.0:
                continue
            crop = get_rotate_crop_image(frame.image, b)
            if digit_likeness(crop, long_side / short_side) >= NUMBER_LIKE:
                add(category, (i,), b, CONF_UNSURE, "ocr_unsure")
                found += 1
                if found >= MAX_UNSURE:
                    break

    # ------------------------------------------------------------------ output
    def _detection(
        self,
        frame: Frame,
        category: Category,
        pts: np.ndarray,
        conf: float,
        reason: str,
        script: str,
        boxes: int,
    ) -> Detection:
        assert self._res is not None
        quad: Quad = to_quad(pts).clamped()
        return Detection(
            detector=self.id,
            category=category,
            quad=quad,
            confidence=float(min(0.99, max(0.05, conf))),
            frame_seq=frame.seq,
            pts=frame.pts,
            completed_at=self._res.clock(),
            extra={"reason": reason, "script": script, "boxes": boxes},
        )
