"""``cards`` detector: Aadhaar (12 digits, Verhoeff-valid) and PAN on the ACCURATE lane.

Flow per frame (spec 5.6): shared OCR pass, then
1. Aadhaar: every 12-digit run (4-4-4 or contiguous, also across two or three boxes on one line, with
   positional OCR-confusion repair) that passes Verhoeff. A leading 0 or 1 is accepted (SPECIMEN).
2. PAN: ``[A-Z]{5}[0-9]{4}[A-Z]`` on the box text (4th character in ``ABCFGHLJPTK`` gives 0.9, else 0.6).
3. Expand each hit to the WHOLE card quad (contour search, then text-block hull, then padded region).
4. ``mask_when_unsure``: a card-shaped quad around digits that almost parse (a misread digit fails the
   checksum) is masked as ``card_shape_unsure``; an ID-1 quad holding >= 6 text boxes but no readable
   number is masked as ``card_like`` (confidence 0.5).

Detections carry codes and numbers only: no digits, no text (``extra`` holds reason/expansion codes).
Tracking and hold are the planner's job (a confirmed card keeps masking 2 s after it is lost).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import ClassVar

import numpy as np

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
from vision.ocr.normalize import digit_ratio, repair_digit_field
from vision.patterns import digit_run_hits, find_masked_aadhaar_hint, find_pan
from vision.patterns.verhoeff import is_valid
from vision.shapes import degraded

from .quad import (
    CardQuad,
    find_card_like,
    find_card_quad,
    find_edge_card,
    find_partial_card_quad,
    quad_overlap,
    region_candidates,
)
from .unsure import find_card_shapes

log = logging.getLogger("vision.cards")

CONF_AADHAAR_VALID = 0.88
CONF_UNSURE = 0.55
CONF_CARD_LIKE = 0.5
CONF_SHAPE = 0.5  # the texture-only fallback (``unsure.py``): same weight as card_like
# the shape fallback is OFF by default until T7.5 measurements say otherwise: see docs/STATUS.md
SHAPE_FALLBACK_DEFAULT = False
CARD_BONUS = 0.06  # a real card outline was found around the number
_HALF_NUMBER = re.compile(r"^\d{4}[ -]?\d{4}$")  # 8 digits in two groups: a 12-digit number with 4 hidden
_PAN_ISH = re.compile(r"^[A-Z]{4,6}[0-9]{3,5}[A-Z]?$")


@dataclass(slots=True)
class _Hit:
    category: Category
    box: np.ndarray  # (4, 2) number box (or union of the boxes the number spans)
    conf: float
    reason: str
    rec_conf: float
    needs_card: bool = False  # only valid if a real card outline was found (unsure hits)
    partial: bool = False  # a number cut by an occluder: the card is inferred from its visible part


def _clean(text: str) -> str:
    return text.strip()


def _digit_candidates(text: str) -> list[str]:
    """The text as read, plus a positionally repaired copy when it looks like a digit field."""
    out = [text]
    compact = text.replace(" ", "")
    if 9 <= len(compact) <= 16 and digit_ratio(text) >= 0.75:
        fixed = repair_digit_field(text)
        if fixed != text:
            out.append(fixed)
    return out


class CardsDetector:
    id: ClassVar[str] = "cards"
    lane: ClassVar[Lane] = Lane.ACCURATE
    categories: ClassVar[frozenset[Category]] = frozenset({Category.AADHAAR, Category.PAN})
    device: ClassVar[Device] = Device.GPU_OPTIONAL  # the shared OCR sessions may run on CUDA
    # OCR det + rec + CUDA context: measured +680 MiB peak (nvidia-smi, RTX 3050)
    vram_mb: ClassVar[int] = 700

    def __init__(self) -> None:
        self._res: Resources | None = None
        self._cfg: DetectorConfig | None = None
        self._shape_fallback = SHAPE_FALLBACK_DEFAULT

    # ------------------------------------------------------------------ plugin interface
    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._cfg, self._res = cfg, res
        self._shape_fallback = bool(cfg.params.get("shape_fallback", SHAPE_FALLBACK_DEFAULT))

    def set_shape_fallback(self, on: bool) -> None:
        """Switch the card-shape fallback (only ever active with ``mask_when_unsure`` on for Aadhaar)."""
        self._shape_fallback = bool(on)

    def warmup(self) -> None:
        warm = getattr(self._res, "warm_ocr", None)
        if warm is not None:
            warm()

    def close(self) -> None:
        self._res = None

    def _unsure_enabled(self, category: Category) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(category)
        return bool(conf is not None and conf.enabled and conf.mask_when_unsure)

    def _enabled(self, category: Category) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(category)
        return bool(conf is not None and conf.enabled)

    # ------------------------------------------------------------------ detection
    def detect(self, frame: Frame, ctx: DetectContext) -> list[Detection]:
        if self._res is None or self._cfg is None:
            raise RuntimeError("cards detector used before configure()")
        want_aadhaar, want_pan = self._enabled(Category.AADHAAR), self._enabled(Category.PAN)
        if not (want_aadhaar or want_pan):
            return []
        ocr = self._res.ocr(frame)  # raises when OCR is unavailable: the lane run fails (PROTECT)
        boxes = [as_array(q) for q in ocr.boxes]
        texts = [_clean(t) for t in ocr.texts]
        confs = list(ocr.confs)
        image = frame.image
        hits: list[_Hit] = []
        masked_hint = any(find_masked_aadhaar_hint(t) for t in texts)

        if want_aadhaar:
            hits.extend(self._aadhaar_hits(boxes, texts, confs))
        if want_pan:
            hits.extend(self._pan_hits(boxes, texts, confs))

        detections: list[tuple[Detection, np.ndarray]] = []
        for hit in hits:
            if hit.category is Category.AADHAAR and not want_aadhaar:
                continue
            card = find_card_quad(image, hit.box, boxes, whole_frame=not hit.needs_card)
            if hit.partial and card.method != "contour":
                part = find_partial_card_quad(image, hit.box, boxes, half_number=True)
                if part is None:
                    continue
                card = part
            elif hit.needs_card and card.method != "contour":
                continue
            detections.append((self._detection(frame, hit, card), card.pts))

        unsure = self._unsure_enabled(Category.AADHAAR) and want_aadhaar
        late = ctx.deadline is not None and self._res.clock() > ctx.deadline
        if unsure and not masked_hint and not late:
            covered = [pts for _, pts in detections]
            cands = region_candidates(image) if len(boxes) >= 1 else []
            for pts, _count in find_card_like(image, boxes, candidates=cands):
                if any(quad_overlap(pts, c) > 0.3 for c in covered):
                    continue
                hit = _Hit(Category.AADHAAR, pts, CONF_CARD_LIKE, "card_like", 0.0)
                detections.append((self._detection(frame, hit, CardQuad(pts, "card_like", 0.0)), pts))
            # a card entering or leaving through a frame side: masked whole, beyond the frame edge
            for pts in find_edge_card(image, boxes, candidates=cands):
                if any(quad_overlap(pts, c) > 0.3 for c in covered):
                    continue
                hit = _Hit(Category.AADHAAR, pts, CONF_CARD_LIKE, "card_like", 0.0)
                det = self._detection(frame, hit, CardQuad(pts, "card_like", 0.0))
                det.extra["expansion"] = "edge"
                detections.append((det, pts))
                covered.append(pts)
        if unsure and self._shape_fallback and not masked_hint and not late and degraded(image):
            covered = [pts for _, pts in detections]
            for pts, bands in find_card_shapes(image, boxes, texts, confs):
                if any(quad_overlap(pts, c) > 0.3 for c in covered):
                    continue
                hit = _Hit(Category.AADHAAR, pts, CONF_SHAPE, "card_shape_texture", 0.0)
                det = self._detection(frame, hit, CardQuad(pts, "card_like", 0.0))
                det.extra["bands"] = bands
                detections.append((det, pts))
        return _dedupe(detections)

    # ------------------------------------------------------------------ hit finders
    def _aadhaar_hits(self, boxes: list[np.ndarray], texts: list[str], confs: list[float]) -> list[_Hit]:
        hits: list[_Hit] = []
        taken: set[int] = set()
        # single boxes
        for i, text in enumerate(texts):
            if not text:
                continue
            valid = False
            partial = False
            half = False
            for cand in _digit_candidates(text):
                runs = digit_run_hits(cand)
                valid = valid or any(r.valid for r in runs)
                digits = sum(c.isdigit() for c in cand)
                partial = partial or (bool(runs) or (9 <= digits <= 16 and digit_ratio(cand) >= 0.75))
                half = half or (_HALF_NUMBER.match(cand.strip()) is not None)
            if valid:
                hits.append(_Hit(Category.AADHAAR, boxes[i], CONF_AADHAAR_VALID, "verhoeff_ok", confs[i]))
                taken.add(i)
            elif half and not partial and self._unsure_enabled(Category.AADHAAR):
                # two 4-digit groups with the rest hidden: kept only when a card region holds them
                hits.append(
                    _Hit(Category.AADHAAR, boxes[i], CONF_UNSURE, "card_shape_partial", confs[i], True, True)
                )
            elif partial and self._unsure_enabled(Category.AADHAAR):
                hits.append(
                    _Hit(Category.AADHAAR, boxes[i], CONF_UNSURE, "card_shape_unsure", confs[i], True)
                )
        # a number split over two or three boxes of one line (grouped 4-4-4 detected as separate boxes)
        for group in group_lines(boxes):
            for size in (2, 3):
                for start in range(0, len(group) - size + 1):
                    idx = group[start : start + size]
                    if any(i in taken for i in idx) or any(not texts[i] for i in idx):
                        continue
                    parts = [
                        repair_digit_field(texts[i]) if digit_ratio(texts[i]) >= 0.75 else texts[i]
                        for i in idx
                    ]
                    if not all(p.replace(" ", "").isdigit() for p in parts):
                        continue
                    joined = "".join(p.replace(" ", "") for p in parts)
                    if len(joined) == 12 and is_valid(joined):
                        hits.append(
                            _Hit(
                                Category.AADHAAR,
                                union_quad([boxes[i] for i in idx]),
                                CONF_AADHAAR_VALID,
                                "verhoeff_ok",
                                float(np.mean([confs[i] for i in idx])),
                            )
                        )
                        taken.update(idx)
        return hits

    def _pan_hits(self, boxes: list[np.ndarray], texts: list[str], confs: list[float]) -> list[_Hit]:
        hits: list[_Hit] = []
        for i, text in enumerate(texts):
            if not text:
                continue
            found = find_pan(text)
            if found:
                best = max(found, key=lambda h: h.confidence)
                reason = "pan_repaired" if best.repaired else "pan_pattern"
                hits.append(_Hit(Category.PAN, boxes[i], best.confidence, reason, confs[i]))
                continue
            compact = re.sub(r"[^A-Za-z0-9]", "", text).upper()
            if self._unsure_enabled(Category.PAN) and 8 <= len(compact) <= 11 and _PAN_ISH.match(compact):
                hits.append(_Hit(Category.PAN, boxes[i], CONF_UNSURE, "card_shape_unsure", confs[i], True))
        return hits

    # ------------------------------------------------------------------ output
    def _detection(self, frame: Frame, hit: _Hit, card: CardQuad) -> Detection:
        assert self._res is not None
        conf = hit.conf * (0.85 + 0.15 * hit.rec_conf) if hit.rec_conf else hit.conf
        if card.method in ("contour", "partial") and hit.reason != "card_like":
            conf += CARD_BONUS
        elif card.method == "padded":
            conf -= 0.05
        conf = float(min(0.99, max(0.05, conf)))
        quad: Quad = to_quad(card.pts).clamped()
        return Detection(
            detector=self.id,
            category=hit.category,
            quad=quad,
            confidence=conf,
            frame_seq=frame.seq,
            pts=frame.pts,
            completed_at=self._res.clock(),
            extra={"reason": hit.reason, "expansion": card.method, "card_aspect": round(card.aspect, 2)},
        )


def _dedupe(items: list[tuple[Detection, np.ndarray]]) -> list[Detection]:
    """One detection per card: when quads overlap (>= 60 %), keep the most confident."""
    kept: list[tuple[Detection, np.ndarray]] = []
    for det, pts in sorted(items, key=lambda t: -t[0].confidence):
        if all(quad_overlap(pts, k) < 0.6 for _, k in kept):
            kept.append((det, pts))
    return [d for d, _ in kept]
