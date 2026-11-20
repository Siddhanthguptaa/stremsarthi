"""The ``plates`` detector with the REAL OCR (CPU) on SPECIMEN vehicles from ``eval`` (T2.1): the T2.8 smoke test.

Skips without the OCR weights (``python models/prepare.py --profile dev``). The scenes are synthetic SPECIMEN
renders (invented registrations on flat vehicle drawings): no real plate, no real data.
"""

from __future__ import annotations

import numpy as np
import pytest

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from models.prepare import DEFAULT_CACHE, artifact_path
from vision.accept_plates import build_item_bgr
from vision.detectors.plates import DETECTOR
from vision.resources import VisionResources
from vision.testing_text import coverage, det_quads

MODELS = ("ppocr-v6-tiny", "ppocr-v5-english-rec")
needs = pytest.mark.skipif(
    not all(artifact_path(n, DEFAULT_CACHE).is_file() for n in MODELS), reason="OCR weights missing"
)
SEED = 1234


@pytest.fixture(scope="module")
def stack():
    cfg = Config(profile="dev", _env_file=None)
    cfg.ocr.rec_budget_ms = (
        5000  # the OCR recognition budget is wall-clock: do not let a busy machine skip crops
    )
    res = VisionResources(cfg)
    det = DETECTOR()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU), res)
    det.warmup()
    yield det
    res.close()


def _detect(det, image, seq: int):
    return det.detect(
        Frame(seq, seq / 4.0, 0.0, image), DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    )


@needs
@pytest.mark.parametrize("index", [0, 5, 10, 15, 20])  # clean-level vehicles: car, bike (two-line), truck, BH
def test_clean_specimen_plates_are_masked_whole(stack, index: int) -> None:
    image, truth = build_item_bgr("plate", index, SEED)
    assert truth["level"] == "clean"
    out = _detect(stack, image, index)
    quads = det_quads(out, Category.PLATE)
    for t in truth["targets"]:
        assert coverage(np.array(t["quad"], np.float32), quads) >= 0.7, (index, t["vehicle"], t["layout"])


@needs
@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5])
def test_plate_shaped_text_that_is_no_registration_is_not_masked(stack, index: int) -> None:
    image, truth = build_item_bgr("neg_plate", index, SEED)
    assert truth["targets"] == []
    assert _detect(stack, image, 100 + index) == []
