"""OCR service: ranking, budget, memoisation, privacy. Real-model tests skip when the weights are absent."""

from __future__ import annotations

import logging
import os

import cv2
import numpy as np
import pytest

from contracts import Config, Frame, OcrProvider
from models.prepare import DEFAULT_CACHE, artifact_path
from vision.ocr import OcrService
from vision.ocr.det import DEFAULT_DET_MODEL, static_shape
from vision.ocr.service import digit_likeness, rank_boxes
from vision.resources import VisionResources
from vision.testing import make_card_scene

WEIGHTS = all(artifact_path(n, DEFAULT_CACHE).is_file() for n in (DEFAULT_DET_MODEL, "ppocr-v5-english-rec"))
needs_weights = pytest.mark.skipif(
    not WEIGHTS, reason="OCR weights not on disk (run models/prepare.py --profile dev)"
)


def _cfg(**kw: object) -> Config:
    return Config(profile="dev", _env_file=None, **kw)  # type: ignore[arg-type]


def test_static_shape_follows_accurate_width() -> None:
    assert static_shape(960) == (544, 960)
    assert static_shape(640) == (352, 640)
    h, w = static_shape(1280)
    assert h % 32 == 0 and w == 1280


def _line(text: str, w: int = 360, h: int = 48) -> np.ndarray:
    img = np.full((h, w, 3), 245, np.uint8)
    cv2.putText(img, text, (6, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (10, 10, 10), 2, cv2.LINE_AA)
    return img


def test_digit_likeness_scores_runs_of_uniform_glyphs_high_and_short_labels_low() -> None:
    """A cheap geometric prior (glyph count, height uniformity, aspect). It cannot tell digits from
    capital letters, so ranking falls back to area for those; it does reject short labels and blanks."""
    for text in ("1234 5678 9012", "ABCDE1234F", "9876543210"):
        assert digit_likeness(_line(text), 7.5) >= 0.6, text
    assert digit_likeness(_line("Hi", 100), 2.0) < 0.6
    assert digit_likeness(np.full((48, 360, 3), 245, np.uint8), 7.5) < 0.6
    assert digit_likeness(np.zeros((0, 0, 3), np.uint8), 5.0) == 0.0


def test_rank_puts_number_like_crops_first_then_larger_area() -> None:
    boxes = np.array(
        [
            [[0, 0], [400, 0], [400, 60], [0, 60]],  # big but not number-like
            [[0, 100], [200, 100], [200, 130], [0, 130]],  # small number-like
            [[0, 200], [300, 200], [300, 240], [0, 240]],  # medium number-like
            [[0, 300], [100, 300], [100, 320], [0, 320]],  # tiny
        ],
        np.float32,
    )
    assert rank_boxes(boxes, [0.1, 0.9, 0.8, 0.2]) == [2, 1, 0, 3]


def test_service_satisfies_the_provider_protocol_without_loading_models(tmp_path) -> None:
    res = VisionResources(_cfg(), cache_dir=tmp_path)
    assert isinstance(res.ocr, OcrProvider)  # lazy: nothing is loaded until the first call


@pytest.fixture(scope="module")
def service() -> OcrService:
    res = VisionResources(_cfg())
    res.warm_ocr()
    yield res.ocr  # type: ignore[misc]
    res.close()


def _frame(scene, seq: int) -> Frame:
    return Frame(seq=seq, pts=seq / 30.0, arrival=0.0, image=scene.image)


@needs_weights
def test_reads_the_specimen_number_and_labels_scripts(service: OcrService) -> None:
    sc = make_card_scene("aadhaar", 1, 61, tilt=6.0, width_frac=0.42)
    res = service(_frame(sc, 1))
    assert len(res.boxes) == len(res.texts) == len(res.confs) == len(res.scripts) >= 4
    assert sc.number_text in res.texts
    i = res.texts.index(sc.number_text)
    assert res.scripts[i] == "latin" and res.confs[i] > 0.8
    cx, cy = np.mean(np.array(res.boxes[i].pts), axis=0)
    assert cv2.pointPolygonTest(sc.number_quad.astype(np.float32), (float(cx), float(cy)), False) >= 0


@needs_weights
def test_pan_number_is_recognised_first_even_with_a_tiny_crop_budget() -> None:
    res = VisionResources(_cfg(ocr={"rec_max_crops": 1, "rec_budget_ms": 10}))
    sc = make_card_scene("pan", 2, 62, tilt=-4.0, width_frac=0.45)
    out = res.ocr(_frame(sc, 1))
    read = [t for t in out.texts if t]
    assert read == [sc.number_text]  # one crop recognised: the number-like one
    unread = [i for i, t in enumerate(out.texts) if not t]
    assert unread and all(out.scripts[i] == "unknown" and 0.0 <= out.confs[i] <= 1.0 for i in unread)
    res.close()


@needs_weights
def test_memoised_per_frame_seq_and_new_frame_recomputes(service: OcrService) -> None:
    sc = make_card_scene("pan", 3, 63)
    f = _frame(sc, 5)
    first = service(f)
    assert not service.last_timing.memo_hit
    again = service(f)
    assert again is first and service.last_timing.memo_hit
    other = make_card_scene("aadhaar", 4, 64)
    third = service(_frame(other, 6))
    assert third is not first and not service.last_timing.memo_hit


@needs_weights
def test_empty_frame_gives_empty_result_and_costs_only_detection(service: OcrService) -> None:
    blank = Frame(seq=900, pts=30.0, arrival=0.0, image=np.full((720, 1280, 3), 90, np.uint8))
    res = service(blank)
    assert res.boxes == () and service.last_timing.recognised == 0 and service.last_timing.boxes == 0


@needs_weights
def test_text_never_reaches_repr_or_logs(service: OcrService, caplog: pytest.LogCaptureFixture) -> None:
    sc = make_card_scene("aadhaar", 5, 65)
    caplog.set_level(logging.DEBUG)
    res = service(_frame(sc, 7))
    assert sc.number_text not in repr(res) and "n_boxes" in repr(res)
    assert sc.number_text not in caplog.text and sc.number_text.replace(" ", "") not in caplog.text


@needs_weights
@pytest.mark.gpu
@pytest.mark.skipif(
    os.environ.get("SS_RUN_GPU") != "1", reason="GPU test: set SS_RUN_GPU=1 and run under the GPU lock"
)
def test_cuda_provider_reads_the_number() -> None:
    cfg = _cfg(gpu={"enabled": True, "ort_provider": "cuda", "mem_limit_mb": 800})
    res = VisionResources(cfg)
    res.warm_ocr()
    sc = make_card_scene("aadhaar", 6, 66)
    out = res.ocr(_frame(sc, 1))
    assert sc.number_text in out.texts
    assert (
        res.factory.metadata[next(iter(res.factory.metadata))]["execution_provider"]
        == "CUDAExecutionProvider"
    )
    res.close()
