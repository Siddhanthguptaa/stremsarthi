"""PP-OCR text-box detector on a fixed-shape graph.

Adapted from IRL Shield ``bench/onnx_detectors.py`` ``PPOCRDetector`` (MIT, Copyright (c) 2026 Elan Nash;
see ``vision/LICENSE.irl-shield``). Changes: the ORT session comes from ``vision.ort.OrtSessionFactory``
(CPU default, CUDA with an arena cap), the static shape follows ``detect.accurate_width``, scores are
returned too, boxes are float quads, and DB thresholds default to the spec values (0.3 / 0.5, unclip 1.6).
Detection runs on the whole frame at the static resolution and boxes are mapped back to the frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from omegaconf import OmegaConf
from rapidocr.ch_ppocr_det import TextDetector
from rapidocr.ch_ppocr_det.utils import DBPostProcess
from rapidocr.utils.typings import EngineType

from models.prepare import load_manifest

DEFAULT_DET_MODEL = "ppocr-v6-tiny"
ALT_DET_MODEL = "ppocr-v5-mobile"
FRAME_W, FRAME_H = 1280, 720


def static_shape(accurate_width: int) -> tuple[int, int]:
    """(height, width) of the static detector graph for ``detect.accurate_width``.

    The same rounding RapidOCR's ``DetPreProcess`` applies (multiple of 32, Python ``round``), so the
    resized frame always fits the fixed graph. 960 gives (544, 960).
    """
    w = max(32, int(round(accurate_width / 32)) * 32)
    h = max(32, int(round((accurate_width * FRAME_H / FRAME_W) / 32)) * 32)
    return h, w


@dataclass(frozen=True, slots=True)
class DetParams:
    thresh: float = 0.3
    box_thresh: float = 0.5
    unclip_ratio: float = 1.6
    max_candidates: int = 1000


#: a second, more sensitive read of the same probability map, used only for mask-all-text (signboards): faint or
#: blurred text that the 0.3 / 0.5 thresholds drop is still a region to cover. No second network pass.
WEAK_PARAMS = DetParams(thresh=0.15, box_thresh=0.3, unclip_ratio=1.6, max_candidates=100)


class TextBoxDetector:
    """``__call__(frame_bgr) -> (boxes (N,4,2) float32 clockwise from top-left, scores (N,))``.

    ``last_weak`` holds the boxes of the sensitive read (``WEAK_PARAMS``) that the normal read did not already
    find, for the same frame."""

    def __init__(self, session: Any, model_name: str, side_len: int, params: DetParams | None = None) -> None:
        art = load_manifest()["artifacts"][model_name]
        p = params or DetParams()
        self.model_name = model_name
        self.params = p
        cfg = OmegaConf.create(
            {
                "limit_side_len": side_len,
                "limit_type": "max",
                "mean": art["preprocess"]["mean"],
                "std": art["preprocess"]["std"],
                "thresh": p.thresh,
                "box_thresh": p.box_thresh,
                "max_candidates": p.max_candidates,
                "unclip_ratio": p.unclip_ratio,
                "use_dilation": True,
                "score_mode": "fast",
            },
            flags={"allow_objects": True},
        )
        cfg.engine_type = EngineType.ONNXRUNTIME
        cfg.session = session  # a verified session bypasses RapidOCR's own model download
        self._detector = TextDetector(cfg)
        self._weak_post = DBPostProcess(
            thresh=WEAK_PARAMS.thresh,
            box_thresh=WEAK_PARAMS.box_thresh,
            max_candidates=WEAK_PARAMS.max_candidates,
            unclip_ratio=WEAK_PARAMS.unclip_ratio,
            use_dilation=True,
            score_mode="fast",
        )
        self.with_weak = True
        self.last_weak: tuple[np.ndarray, np.ndarray] = _EMPTY

    def __call__(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        det = self._detector
        self.last_weak = _EMPTY
        shape = frame_bgr.shape[0], frame_bgr.shape[1]
        pre = det.get_preprocess()(frame_bgr)
        if pre is None:
            return _EMPTY
        preds = det.session(pre)
        boxes, scores = det.postprocess_op(preds, shape)
        strong = np.asarray(boxes, dtype=np.float32).reshape(-1, 4, 2)
        strong_s = np.asarray(scores, dtype=np.float32).reshape(-1)
        if self.with_weak:
            wb, ws = self._weak_post(preds, shape)
            self.last_weak = _new_boxes(
                np.asarray(wb, dtype=np.float32).reshape(-1, 4, 2),
                np.asarray(ws, dtype=np.float32).reshape(-1),
                strong,
            )
        if len(strong) == 0:
            return _EMPTY
        order = det.sorted_boxes(strong)  # same order as RapidOCR's own output
        if len(order) == len(strong):
            idx = _match_order(strong, order)
            strong, strong_s = order, strong_s[idx]
        return strong, strong_s


_EMPTY: tuple[np.ndarray, np.ndarray] = (np.zeros((0, 4, 2), np.float32), np.zeros((0,), np.float32))


def _match_order(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Index into ``before`` of every row of ``after`` (``sorted_boxes`` only permutes rows)."""
    key = {tuple(np.round(b.ravel(), 3)): i for i, b in enumerate(before)}
    return np.array([key[tuple(np.round(a.ravel(), 3))] for a in after], dtype=int)


def _new_boxes(wb: np.ndarray, ws: np.ndarray, strong: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The weak boxes that are not (mostly) covered by a normal box."""
    if len(wb) == 0:
        return _EMPTY
    if len(strong) == 0:
        return wb, ws
    sx1, sy1 = strong[:, :, 0].min(1), strong[:, :, 1].min(1)
    sx2, sy2 = strong[:, :, 0].max(1), strong[:, :, 1].max(1)
    keep = []
    for i, b in enumerate(wb):
        x1, y1, x2, y2 = b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max()
        inter = np.clip(np.minimum(x2, sx2) - np.maximum(x1, sx1), 0, None) * np.clip(
            np.minimum(y2, sy2) - np.maximum(y1, sy1), 0, None
        )
        if float(inter.max()) < 0.4 * max((x2 - x1) * (y2 - y1), 1e-6):
            keep.append(i)
    return wb[keep], ws[keep]
