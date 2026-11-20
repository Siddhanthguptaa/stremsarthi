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


class TextBoxDetector:
    """``__call__(frame_bgr) -> (boxes (N,4,2) float32 clockwise from top-left, scores (N,))``."""

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

    def __call__(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        out = self._detector(frame_bgr)
        if out.boxes is None or len(out.boxes) == 0:
            return np.zeros((0, 4, 2), np.float32), np.zeros((0,), np.float32)
        return np.asarray(out.boxes, dtype=np.float32), np.asarray(out.scores, dtype=np.float32)
