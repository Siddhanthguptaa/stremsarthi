"""``screens`` detector: OTP / notification screens and BGMI / Free Fire IDs and room credentials (ACCURATE lane).

Flow per frame (spec 5.10, plan D-11): the shared OCR pass (``read_ocr``: the one OCR pass of the lane plus the Indic
enrichment of its weak boxes), then

1. OTP screens (``OTP_SCREEN``): a 4-8 digit code next to a cue word (``otp``, ``verification code``, ``do not
   share``, ``ओटीपी``...) -> its banner, or the whole phone screen when a phone-shaped rectangle around the banner
   holds enough text boxes (``otp.py``).
2. Game screens (``GAME_ID``, ``ROOM_CREDENTIAL``): two routes that confirm each other (``games.py``,
   ``templates.py``). The template route finds a fixed UI anchor (multi-scale ``matchTemplate``) and masks the
   regions relative to it without reading them; the label route reads ``ID:`` / ``ROOM ID`` / ``PASSWORD`` labels
   and validates the value's shape. A field found by both is one detection over the union of the two boxes.

Detections carry codes and numbers only (``extra``: reason, source, boxes), never text. Tracking and hold are the
planner's job. The shipped templates are derived from SPECIMEN overlays, not from the real games (UNVERIFIED,
calibrate on the streamer's phone before trusting the template route against them).
"""

from __future__ import annotations

import logging
from pathlib import Path
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
from vision.ocr.layout import as_array, side_lengths, to_quad
from vision.ocr.multi import install_indic, read_ocr
from vision.ocr.normalize import normalise_text
from vision.shapes import degraded

from .games import find_game_fields, has_game_context
from .otp import banner_quad, find_otps, phone_screen_quad, text_rect
from .templates import GameTemplate, TemplateHit, TemplateMatcher, load_templates
from .unsure import CONF as UNSURE_CONF
from .unsure import find_banner_shapes, relaxed_hits, relaxed_matcher

log = logging.getLogger("vision.screens")

CATEGORIES = frozenset({Category.OTP_SCREEN, Category.GAME_ID, Category.ROOM_CREDENTIAL})
GAME_CATEGORIES = (Category.GAME_ID, Category.ROOM_CREDENTIAL)
MAX_BOXES = 150  # paragraph grouping is quadratic: cap on boxes
CONF_TEMPLATE, CONF_BOTH = 0.7, 0.95
# T7.5: the banner-shape fallback (``unsure.py``) is ON by default (0 false masks on the negative sets, see docs/STATUS.md);
# the relaxed-template fallback is OFF (no recall gain, false masks)
SHAPE_FALLBACK_DEFAULT = True  # the banner-shape fallback; the relaxed-template one is separate and OFF


def _rect_quad(x1: float, y1: float, x2: float, y2: float) -> np.ndarray:
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float32)


def _bbox(pts: np.ndarray) -> tuple[float, float, float, float]:
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class ScreensDetector:
    id: ClassVar[str] = "screens"
    lane: ClassVar[Lane] = Lane.ACCURATE
    categories: ClassVar[frozenset[Category]] = CATEGORIES
    device: ClassVar[Device] = Device.GPU_OPTIONAL  # the shared OCR sessions may run on CUDA
    # no model of its own: the OCR service is shared and its VRAM is declared (and counted) by ``cards``
    vram_mb: ClassVar[int] = 0

    def __init__(self) -> None:
        self._res: Resources | None = None
        self._cfg: DetectorConfig | None = None
        self._matcher = TemplateMatcher([])
        self._screen_mask = True
        self._screen_min_boxes = 5
        self._shape_fallback = SHAPE_FALLBACK_DEFAULT
        self._fb_banner, self._fb_template = True, False
        self._relaxed = TemplateMatcher([])

    # ------------------------------------------------------------------ plugin interface
    def configure(self, cfg: DetectorConfig, res: Resources) -> None:
        self._cfg, self._res = cfg, res
        params = cfg.params
        templates = params.get("templates")
        if templates is None:
            directory = params.get("templates_dir")
            templates = load_templates(Path(directory) if directory else None)
        self._matcher = TemplateMatcher(list(templates) if not isinstance(templates, list) else templates)
        self._screen_mask = bool(params.get("screen_mask", True))
        self._screen_min_boxes = int(params.get("screen_min_boxes", 5))
        self._shape_fallback = bool(params.get("shape_fallback", SHAPE_FALLBACK_DEFAULT))
        self._fb_banner = bool(params.get("banner_fallback", True))
        self._fb_template = bool(params.get("template_fallback", False))
        self._relaxed = relaxed_matcher(self._matcher.templates)
        install_indic(res)  # no-op for a fake OCR provider; idempotent

    def set_shape_fallback(self, on: bool, *, banner: bool = True, template: bool = True) -> None:
        """Switch the banner-shape and relaxed-template fallbacks (each needs ``mask_when_unsure`` for its category)."""
        self._shape_fallback = bool(on)
        self._fb_banner, self._fb_template = bool(banner), bool(template)

    def warmup(self) -> None:
        warm = getattr(self._res, "warm_ocr", None)
        if warm is not None:
            warm()
        enricher = getattr(self._res, "ocr_indic", None)
        if enricher is not None:
            enricher.warmup()

    def close(self) -> None:
        self._res = None

    @property
    def templates(self) -> list[GameTemplate]:
        return self._matcher.templates

    # ------------------------------------------------------------------ helpers
    def _enabled(self, category: Category) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(category)
        return bool(conf is not None and conf.enabled)

    def _unsure(self, category: Category) -> bool:
        assert self._cfg is not None
        conf = self._cfg.categories.get(category)
        return bool(conf is not None and conf.enabled and conf.mask_when_unsure)

    # ------------------------------------------------------------------ detection
    def detect(self, frame: Frame, ctx: DetectContext) -> list[Detection]:
        if self._res is None or self._cfg is None:
            raise RuntimeError("screens detector used before configure()")
        want_otp = self._enabled(Category.OTP_SCREEN)
        want_games = {c for c in GAME_CATEGORIES if self._enabled(c)}
        if not want_otp and not want_games:
            return []
        template_hits: list[TemplateHit] = []
        if want_games:
            template_hits = self._matcher.match(frame.image)
        ocr = read_ocr(self._res, frame)  # raises when OCR is unavailable: the lane run fails (PROTECT)
        boxes = [as_array(q) for q in ocr.boxes]
        texts = [normalise_text(t).strip() for t in ocr.texts]
        confs = [float(c) for c in ocr.confs]
        heights = [side_lengths(b)[1] for b in boxes]
        if len(boxes) > MAX_BOXES:  # keep the biggest text: a code or an ID is large next to clutter
            keep = sorted(range(len(boxes)), key=lambda i: -heights[i])[:MAX_BOXES]
            keep.sort()
            boxes = [boxes[i] for i in keep]
            texts = [texts[i] for i in keep]
            confs = [confs[i] for i in keep]
            heights = [heights[i] for i in keep]
        out: list[Detection] = []
        if want_otp and boxes:
            out += self._otp(frame, boxes, texts, confs, heights)
        if want_games:
            out += self._games(frame, boxes, texts, confs, heights, template_hits, want_games)
        if self._shape_fallback and degraded(frame.image):
            late = ctx.deadline is not None and self._res.clock() > ctx.deadline
            if not late:
                out += self._unsure_shapes(
                    frame, boxes, texts, confs, template_hits, want_otp, want_games, out
                )
        return out

    # ------------------------------------------------------------------ mask-when-unsure fallbacks
    def _unsure_shapes(
        self,
        frame: Frame,
        boxes: list[np.ndarray],
        texts: list[str],
        confs: list[float],
        strict: list[TemplateHit],
        want_otp: bool,
        want_games: set[Category],
        found: list[Detection],
    ) -> list[Detection]:
        out: list[Detection] = []
        taken = [np.array(d.quad.pts, np.float32) for d in found]

        def fresh(pts: np.ndarray) -> bool:
            return all(_iou(_bbox(pts), _bbox(t)) < 0.3 for t in taken)

        if want_otp and self._fb_banner and self._unsure(Category.OTP_SCREEN):
            for pts, bands in find_banner_shapes(frame.image, boxes, texts, confs):
                if fresh(pts):
                    out.append(
                        self._detection(
                            frame,
                            Category.OTP_SCREEN,
                            pts,
                            UNSURE_CONF,
                            {"reason": "banner_shape_unsure", "shape": "banner", "bands": bands},
                        )
                    )
        wanted = {c for c in want_games if self._unsure(c)}
        if wanted and self._fb_template:
            for hit in relaxed_hits(self._relaxed, strict, frame.image, boxes, texts, confs):
                for cat, quad in hit.regions:
                    if cat in wanted and fresh(quad):
                        out.append(
                            self._detection(
                                frame,
                                cat,
                                quad,
                                UNSURE_CONF,
                                {"reason": "template_unsure", "source": "template_relaxed", "game": hit.game},
                            )
                        )
        return out

    # ------------------------------------------------------------------ OTP screens
    def _otp(
        self,
        frame: Frame,
        boxes: list[np.ndarray],
        texts: list[str],
        confs: list[float],
        heights: list[float],
    ) -> list[Detection]:
        found: list[tuple[float, np.ndarray, str, int]] = []
        for hit in find_otps(boxes, texts, heights, confs, cue_only=self._unsure(Category.OTP_SCREEN)):
            idx = (*hit.cue_idx, *(() if hit.code_idx is None else (hit.code_idx,)))
            rect = text_rect(boxes, idx)
            h = float(np.mean([heights[i] for i in idx]))
            quad = banner_quad(frame.image, rect, h)
            reason = "otp_banner"
            if self._screen_mask:
                screen = phone_screen_quad(frame.image, rect, boxes, self._screen_min_boxes)
                if screen is not None:
                    quad, reason = screen, "otp_phone_screen"
            ocr_conf = float(np.mean([confs[i] for i in idx]))
            conf = hit.conf * (0.9 + 0.1 * min(1.0, ocr_conf))
            found.append((conf, quad, f"{reason}:{hit.reason}", len(idx)))
        kept: list[tuple[float, np.ndarray, str, int]] = []
        for item in sorted(found, key=lambda t: -t[0]):  # one detection per screen or banner
            if all(_iou(_bbox(item[1]), _bbox(k[1])) < 0.6 for k in kept):
                kept.append(item)
        out = []
        for conf, quad, reason, n in kept:
            shape, why = reason.split(":")
            out.append(
                self._detection(
                    frame, Category.OTP_SCREEN, quad, conf, {"reason": why, "shape": shape, "boxes": n}
                )
            )
        return out

    # ------------------------------------------------------------------ game screens
    def _games(
        self,
        frame: Frame,
        boxes: list[np.ndarray],
        texts: list[str],
        confs: list[float],
        heights: list[float],
        template_hits: list[TemplateHit],
        want: set[Category],
    ) -> list[Detection]:
        strong = any(not h.weak for h in template_hits)
        # a weak anchor (generic shape) needs a strong anchor of the frame or game cue words in the text
        evidence = strong or has_game_context([t.upper() for t in texts if t])
        template_hits = [h for h in template_hits if evidence or not h.weak]
        fields = []
        if boxes:
            fields = find_game_fields(
                boxes,
                texts,
                heights,
                confs,
                unsure={c: self._unsure(c) for c in GAME_CATEGORIES},
                template_context=strong,
            )
        fields = [f for f in fields if f.category in want]
        regions: list[tuple[Category, np.ndarray, str, float]] = []
        for hit in sorted(template_hits, key=lambda h: -h.score):
            for cat, quad in hit.regions:
                # two templates of one layout (the same card in two games) place the same region: keep the best
                if cat in want and all(
                    c is not cat or _iou(_bbox(q), _bbox(quad)) < 0.6 for c, q, _g, _s in regions
                ):
                    regions.append((cat, quad, hit.game, hit.score))
        out: list[Detection] = []
        matched_regions: set[int] = set()
        for f in fields:
            box = boxes[f.idx]
            cx, cy = box.mean(axis=0)
            x1, y1, x2, y2 = _bbox(box)
            source, extra_game = "ocr", ""
            conf = f.conf * (0.85 + 0.15 * min(1.0, confs[f.idx]))
            for r, (cat, rq, game, _score) in enumerate(regions):
                rx1, ry1, rx2, ry2 = _bbox(rq)
                if cat is f.category and rx1 <= cx <= rx2 and ry1 <= cy <= ry2:
                    matched_regions.add(r)
                    x1, y1, x2, y2 = min(x1, rx1), min(y1, ry1), max(x2, rx2), max(y2, ry2)
                    source, extra_game, conf = "template_ocr", game, max(conf, CONF_BOTH)
                    break
            out.append(
                self._detection(
                    frame,
                    f.category,
                    _rect_quad(x1, y1, x2, y2),
                    conf,
                    {"reason": f.reason, "source": source, "game": extra_game, "boxes": 1},
                )
            )
        for r, (cat, rq, game, score) in enumerate(regions):
            if r in matched_regions:
                continue
            conf = CONF_TEMPLATE + 0.2 * max(0.0, min(1.0, (score - 0.6) / 0.4))
            out.append(
                self._detection(
                    frame,
                    cat,
                    rq,
                    conf,
                    {"reason": "template_region", "source": "template", "game": game, "boxes": 0},
                )
            )
        return out

    # ------------------------------------------------------------------ output
    def _detection(
        self, frame: Frame, category: Category, pts: np.ndarray, conf: float, extra: dict[str, object]
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
            extra=extra,
        )
