"""The ``text`` detector with the REAL OCR (CPU) on rendered SPECIMEN scenes: smoke tests and the T2.4 acceptance.

Skips without the OCR weights (``python models/prepare.py --profile dev``) or the Indic test fonts
(``vision/tests/fetch_text_fonts.py`` or T2.1's ``eval/fonts``). All numbers and IDs on the images are invented.
"""

from __future__ import annotations

import pytest

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from models.prepare import DEFAULT_CACHE, artifact_path
from vision import testing_text as tt
from vision.accept_text import build_scenes, run
from vision.detectors.cards import DETECTOR as CARDS
from vision.detectors.text import DETECTOR as TEXT
from vision.resources import VisionResources

MODELS = (
    "ppocr-v6-tiny",
    "ppocr-v5-english-rec",
    "ppocr-v5-devanagari-rec",
    "ppocr-v5-tamil-rec",
    "ppocr-v5-telugu-rec",
)
WEIGHTS = all(artifact_path(n, DEFAULT_CACHE).is_file() for n in MODELS)
FONTS = tt.fonts_available("devanagari", "tamil", "telugu", "kannada", "bengali")
needs = pytest.mark.skipif(not (WEIGHTS and FONTS), reason="OCR weights or Indic test fonts missing")


@pytest.fixture(scope="module")
def stack():
    cfg = Config(profile="dev", _env_file=None)
    res = VisionResources(cfg)
    dcfg = DetectorConfig(categories=cfg.categories, device=Device.CPU)
    text, cards = TEXT(), CARDS()
    for d in (cards, text):
        d.configure(dcfg, res)
    text.warmup()
    cards.warmup()
    yield res, text, cards
    res.close()


def _detect(stack, scene: tt.TextScene, seq: int):
    _, text, _ = stack
    ctx = DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    return list(text.detect(Frame(seq, seq / 4.0, 0.0, scene.image), ctx))


@needs
@pytest.mark.parametrize("index", [0, 1, 2, 3])  # phone, PIN, UPI ID, e-mail on Latin notes
def test_latin_notes_are_masked_per_category(stack, index: int) -> None:
    scene = tt.make_pii_scene(index, 5)
    out = _detect(stack, scene, index)
    outcome = tt.score_text_scene(scene, out)
    assert all(outcome.item_hit), (
        index,
        [(i.category.value, ok) for i, ok in zip(scene.items, outcome.item_hit, strict=True)],
    )


@needs
def test_devanagari_numerals_and_label_are_read_through_the_indic_enricher(stack) -> None:
    for index in (4, 5):  # Devanagari phone and PIN notes
        scene = tt.make_pii_scene(index, 5)
        outcome = tt.score_text_scene(scene, _detect(stack, scene, 100 + index))
        assert all(outcome.item_hit), index


@needs
@pytest.mark.parametrize("index", [0, 1, 2, 3, 4])  # Devanagari, Kannada, Tamil, Telugu, Bengali boards
def test_signboards_in_every_script_are_fully_masked_as_signboards(stack, index: int) -> None:
    scene = tt.make_sign_scene(index, 5)
    out = _detect(stack, scene, 200 + index)
    outcome = tt.score_text_scene(scene, out)
    assert all(outcome.line_covered), (scene.params, outcome.line_covered)
    assert outcome.false_sign == 0 and not any(d.category is not Category.SIGNBOARD for d in out)


@needs
def test_lookalike_numbers_and_ids_are_not_masked_as_pii(stack) -> None:
    for index in range(4):
        scene = tt.make_negative_scene(index, 5)
        out = _detect(stack, scene, 300 + index)
        assert [d for d in out if d.category is not Category.SIGNBOARD] == [], (index, scene.line_texts)


@needs
@pytest.mark.slow
def test_acceptance_t24_text_pii_and_signboards_cpu() -> None:
    """T2.4: >= 60 images; phone/PIN/UPI/e-mail recall >= 90 % (Tamil/Telugu numerals reported apart),
    signboard text recall >= 85 %, hard negatives clean; cards + text lane p95 <= 450 ms (x1.5 slack for a
    shared dev machine, the measured numbers are printed)."""
    scenes = build_scenes(48, 40, 25, seed=1)
    assert len(scenes) >= 60
    result = run(scenes)
    print({k: v for k, v in result.items() if k not in ("recall_by_category_script",)})
    assert result["pii_recall_excluding_tamil_telugu_numerals"] >= 0.90, result["missed_items"]
    assert result["pii_masked_by_any_category"] >= 0.95  # nothing sensitive is left visible
    assert result["signboard_text_recall"] >= 0.85
    assert result["hard_negative_frames_with_pii_mask"] == 0
    assert result["pii_precision"] >= 0.85, result["false_pii_detections"]
    assert result["signboard_false_detections"] == 0
    lat = result["cards_plus_text_ms"]
    assert lat["p95"] <= 450.0 * 1.5, lat
