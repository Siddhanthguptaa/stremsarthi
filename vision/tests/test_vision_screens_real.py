"""The ``screens`` detector with the REAL OCR (CPU) on SPECIMEN scenes from ``eval`` (T2.1): the T2.9 smoke test.

Skips without the OCR weights (``python models/prepare.py --profile dev``). The scenes are synthetic SPECIMEN
renders (invented codes and IDs on our own phone and game UIs): no real notification, no real game, no real data.
"""

from __future__ import annotations

import numpy as np
import pytest

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from models.prepare import DEFAULT_CACHE, artifact_path
from vision.accept_plates import build_item_bgr
from vision.detectors.screens import DETECTOR
from vision.resources import VisionResources
from vision.testing_screens import coverage_of

MODELS = ("ppocr-v6-tiny", "ppocr-v5-english-rec")
needs = pytest.mark.skipif(
    not all(artifact_path(n, DEFAULT_CACHE).is_file() for n in MODELS), reason="OCR weights missing"
)
SEED = 1234
TRUTH = {
    "otp_banner": Category.OTP_SCREEN,
    "game_player_id": Category.GAME_ID,
    "game_room_id": Category.ROOM_CREDENTIAL,
    "game_room_password": Category.ROOM_CREDENTIAL,
}


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


def _covered(dets, truth) -> list[bool]:
    out = []
    for t in truth["targets"]:
        if t.get("aux") or t["category"] not in TRUTH or not t["visible"]:
            continue
        quads = [np.array(d.quad.pts, np.float32) for d in dets if d.category is TRUTH[t["category"]]]
        out.append(coverage_of(t["quad"], quads) >= 0.7)
    return out


@needs
@pytest.mark.parametrize("index", [5, 10, 15, 20, 25])  # clean-level phone screens: sms, lock, entry layouts
def test_clean_specimen_otp_screens_are_masked(stack, index: int) -> None:
    image, truth = build_item_bgr("otp_screen", index, SEED)
    assert truth["level"] == "clean"
    got = _covered(_detect(stack, image, index), truth)
    assert got and all(got), (index, truth["sprite_meta"]["layout"], truth["sprite_meta"]["lang"])


@needs
@pytest.mark.parametrize("bucket", ["game_bgmi", "game_freefire"])
@pytest.mark.parametrize("index", [0, 5, 10])  # clean-level lobbies
def test_clean_specimen_game_screens_mask_id_room_and_password(stack, bucket: str, index: int) -> None:
    image, truth = build_item_bgr(bucket, index, SEED)
    assert truth["level"] == "clean"
    got = _covered(_detect(stack, image, index), truth)
    assert len(got) == 3 and all(got), (bucket, index)


@needs
@pytest.mark.parametrize("bucket", ["neg_screen", "neg_game"])
@pytest.mark.parametrize("index", [0, 1, 2, 3, 5, 6])
def test_screens_and_games_without_secrets_are_not_masked(stack, bucket: str, index: int) -> None:
    image, _truth = build_item_bgr(bucket, index, SEED)
    assert _detect(stack, image, index) == []
