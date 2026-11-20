"""PP-OCR line recogniser (Latin by default; the same class serves devanagari/ta/te in T2.4).

Adapted from IRL Shield ``bench/onnx_detectors.py`` ``PPOCRRecognizer`` (MIT, Copyright (c) 2026 Elan Nash;
see ``vision/LICENSE.irl-shield``). Changes: session from ``OrtSessionFactory``, crops are passed in by the
OCR service (which ranks them and enforces the budget), and only the recogniser is wrapped here.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from omegaconf import OmegaConf
from rapidocr.ch_ppocr_rec import TextRecInput, TextRecognizer
from rapidocr.utils.typings import EngineType

LATIN_REC_MODEL = "ppocr-v5-english-rec"


class LineRecogniser:
    """``__call__(crops_bgr) -> (texts, confidences)`` for a list of cropped text-line images."""

    def __init__(self, session: Any, lang: str = "en", batch: int = 4) -> None:
        if "character" not in session.get_modelmeta().custom_metadata_map:
            raise RuntimeError("recogniser model is missing its embedded character dictionary")
        cfg = OmegaConf.create(
            {
                "engine_type": EngineType.ONNXRUNTIME,
                "lang_type": lang,
                "rec_batch_num": batch,
                "rec_img_shape": [3, 48, 320],
                "rec_keys_path": None,
                "font_path": None,
            },
            flags={"allow_objects": True},
        )
        cfg.session = session
        self._rec = TextRecognizer(cfg)

    def __call__(self, crops: list[np.ndarray]) -> tuple[list[str], list[float]]:
        if not crops:
            return [], []
        out = self._rec(TextRecInput(img=crops))
        return [str(t) for t in out.txts], [float(max(0.0, min(1.0, s))) for s in out.scores]
