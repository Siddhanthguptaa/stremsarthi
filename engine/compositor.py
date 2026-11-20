"""The mask compositor: turns a ``FrameDecision`` into the pixels that leave the engine.

Adapted from IRL Shield ``shield/render.py`` (MIT, Copyright (c) 2026 Elan Nash;
``engine/THIRD_PARTY_LICENSES/irl-shield/LICENSE``). Kept: PROTECT's mean-colour fill (a 1x1
resize blown back up, so no spatial shape survives), the full-frame slate for HOLD and for a long
PROTECT, convex quads rasterised on ROI crops. Changed: BLUR, PIXELATE and SOLID styles instead of
a single opaque black (see ``engine/blur.py``); masks arrive already padded and projected from
``FrameDecision.masks`` (vision pads, the motion path projects); FULL_FRAME requests and a masked
area above ``engine.max_mask_area_ratio`` collapse to one solid fill; slates and the fence come
from ``engine/slate.py``.

Rules (docs/specs/engine.md 5.3 and 5.5): LIVE paints masks, PROTECT shows the mean colour for the
first ``engine.protect_fill_s`` and then the slate, HOLD shows the slate. The decision's ``image`` is
the shared read-only ring frame: ``compose`` never mutates it and always returns a new array.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from contracts import Category, Config, MaskStyle

from .blur import SOLID_BGR, paint_polygon, polygon_union_ratio, quad_polygon
from .control import HOLD, LIVE, PROTECT
from .deadline import FrameDecision
from .mouth import MIN_MOUTH_KERNEL
from .slate import SlateKind, SlatePresenter, SlateStore

Kind = Literal["frame", "mean", "slate", "solid"]

_STRICTNESS = {MaskStyle.BLUR: 0, MaskStyle.PIXELATE: 1, MaskStyle.SOLID: 2}


@dataclass(slots=True)
class Composed:
    image: np.ndarray
    kind: Kind
    state: str  # what actually left the engine: LIVE, PROTECT or HOLD (the fence can upgrade LIVE to HOLD)
    slate: SlateKind | None
    quads_painted: int
    masked_ratio: float  # upper estimate (sum of bounding boxes), capped at 1
    collapsed: bool  # FULL_FRAME or area above the limit: one solid fill

    @property
    def audio_silent(self) -> bool:
        return self.state == HOLD


class Compositor:
    def __init__(
        self,
        config: Config | None = None,
        *,
        presenter: SlatePresenter | None = None,
        max_area_ratio: float | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        canvas = config.canvas if config else (1280, 720)
        self.width, self.height = width or canvas[0], height or canvas[1]
        self.presenter = presenter or SlatePresenter(SlateStore(config, self.width, self.height))
        self.max_area_ratio = (
            max_area_ratio
            if max_area_ratio is not None
            else (config.engine.max_mask_area_ratio if config else 0.6)
        )
        self.style_overrides: dict[Category, MaskStyle] = {}
        self.stats = {"frames": 0, "quads": 0, "collapsed": 0}

    # ------------------------------------------------------------------ public

    def compose(self, decision: FrameDecision, capture_highwater: int = -1) -> Composed:
        self.stats["frames"] += 1
        image = decision.image
        shape_ok = (
            isinstance(image, np.ndarray) and image.shape[:2] == (self.height, self.width) and image.ndim == 3
        )
        kind = self.presenter.present(decision.state, decision.reason, decision.seq, capture_highwater)
        if kind is None and not shape_ok:
            kind = "FAILURE"  # nothing safe to paint on: never pass an unexpected frame through
        if kind is not None:
            state = decision.state if decision.state != LIVE else HOLD
            if state == PROTECT and decision.presentation == "mean" and shape_ok:
                return Composed(self._mean_fill(image), "mean", PROTECT, kind, 0, 0.0, False)  # type: ignore[arg-type]
            return Composed(self.presenter.store.get(kind).copy(), "slate", state, kind, 0, 0.0, False)
        assert isinstance(image, np.ndarray)
        return self._paint(image, decision)

    # ------------------------------------------------------------------ internals

    def _mean_fill(self, image: np.ndarray) -> np.ndarray:
        b, g, r, _ = cv2.mean(image)  # one number per channel: no spatial detail survives
        return self._flat((round(b), round(g), round(r)), image.shape[1], image.shape[0])

    @staticmethod
    def _flat(bgr: tuple[int, int, int], width: int, height: int) -> np.ndarray:
        """A new height x width frame of one colour (a 1x1 resize is 20x faster than a broadcast fill)."""
        return cv2.resize(np.array([[bgr]], np.uint8), (width, height), interpolation=cv2.INTER_NEAREST)

    def _solid(self) -> np.ndarray:
        return self._flat(SOLID_BGR, self.width, self.height)

    def _paint(self, image: np.ndarray, decision: FrameDecision) -> Composed:
        jobs: list[tuple[int, MaskStyle, Category, np.ndarray, int]] = []
        for projected in decision.masks:
            request = projected.request
            style = self.style_overrides.get(request.category, request.style)
            for quad in projected.quads:
                jobs.append(
                    (_STRICTNESS[style], style, request.category, np.asarray(quad.pts, np.float64), 0)
                )
        for quad in decision.mouth_quads:  # MOUTH_OF_SPEAKER: always a strong blur, whatever the face style
            jobs.append(
                (0, MaskStyle.BLUR, Category.FACE, np.asarray(quad.pts, np.float64), MIN_MOUTH_KERNEL)
            )
        if decision.full_frame:
            self.stats["collapsed"] += 1
            return Composed(self._solid(), "solid", LIVE, None, len(jobs), 1.0, True)
        frame_area = float(self.width * self.height)
        bbox_sum = sum(
            float((p[:, 0].max() - p[:, 0].min()) * (p[:, 1].max() - p[:, 1].min())) for _, _, _, p, _ in jobs
        )
        ratio = min(1.0, bbox_sum / frame_area)
        if ratio > self.max_area_ratio:
            polys = [quad_polygon(p, self.width, self.height) for _, _, _, p, _ in jobs]
            if polygon_union_ratio(polys, self.width, self.height) > self.max_area_ratio:
                self.stats["collapsed"] += 1
                return Composed(self._solid(), "solid", LIVE, None, len(jobs), 1.0, True)
        out = image.copy()
        painted = 0
        # Least strict first, so a stricter style on an overlap is never blended away.
        for _, style, category, points, min_kernel in sorted(jobs, key=lambda j: j[0]):
            if paint_polygon(out, points, style, category, min_kernel=min_kernel):
                painted += 1
        self.stats["quads"] += painted
        return Composed(out, "frame", LIVE, None, painted, ratio, False)
