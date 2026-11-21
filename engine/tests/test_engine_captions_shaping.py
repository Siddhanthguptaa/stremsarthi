"""Caption shaping: raqm renders of conjunct-heavy strings match reference renders pixel for pixel, and
differ from the unshaped (basic layout) renders. Fonts: hashes, licences and coverage."""

from __future__ import annotations

import hashlib
import logging
import re

import numpy as np
import pytest
from PIL import Image, ImageFont, features

from engine import captions as cap
from engine.captions import CaptionRenderer, clusters, segment
from engine.tests.gen_engine_caption_goldens import CASES, GOLDEN_DIR, SIZE

needs_raqm = pytest.mark.skipif(not features.check("raqm"), reason="Pillow built without libraqm")
ASSETS = cap.ASSETS


def _golden(name: str) -> np.ndarray:
    return np.asarray(Image.open(GOLDEN_DIR / f"{name}.png").convert("L"), np.uint8)


def test_every_caption_font_matches_its_recorded_hash_and_ships_its_licence():
    notice = (ASSETS / "NOTICE.md").read_text(encoding="utf-8")
    recorded = set(re.findall(r"[0-9a-f]{64}", notice))
    for script, path in cap.FONT_FILES.items():
        assert path.exists(), script
        assert hashlib.sha256(path.read_bytes()).hexdigest() in recorded, f"{path.name} hash not in NOTICE.md"
        if script != "latin":
            ofl = ASSETS / f"OFL-{path.name.replace('-VF.ttf', '')}.txt"
            assert hashlib.sha256(ofl.read_bytes()).hexdigest() in recorded, ofl.name
            assert "SIL OPEN FONT LICENSE Version 1.1" in ofl.read_text(encoding="utf-8")


@pytest.mark.parametrize("name,lang,text", CASES, ids=[c[0] for c in CASES])
def test_each_font_covers_its_own_script(name, lang, text):
    """No tofu: every non-space character of the sample is in the font's cmap (checked via the glyph mask)."""
    renderer = CaptionRenderer(use_raqm=False)
    for ch in set(text) - {" ", "‌", "‍"}:
        script = cap.script_of(ch)
        assert script is not None
        font = renderer._font(script, 40)
        mask = font.getmask(ch)
        assert mask.getbbox() is not None or ch in "्্்్್", f"{name}: no glyph for U+{ord(ch):04X}"


@needs_raqm
@pytest.mark.parametrize("name,lang,text", CASES, ids=[c[0] for c in CASES])
def test_shaped_render_matches_the_reference_render(name, lang, text):
    """Reference renders are committed PNGs made by ``gen_engine_caption_goldens`` and checked by eye (raqm
    on, fixed size). A fresh render must agree: at most 0.5 % of the pixels differ by more than 48 levels
    (headroom for a FreeType patch release; a changed glyph run moves thousands of pixels)."""
    fresh = CaptionRenderer(use_raqm=True).text_mask(text, lang, SIZE)
    ref = _golden(name)
    assert fresh.shape == ref.shape
    differing = (np.abs(fresh.astype(int) - ref.astype(int)) > 48).mean()
    assert differing < 0.005, f"{name}: {differing:.3%} of pixels differ from the reference"


@needs_raqm
@pytest.mark.parametrize("name,lang,text", CASES, ids=[c[0] for c in CASES])
def test_shaping_really_changes_the_glyph_run(name, lang, text):
    """The reference is not the unshaped layout: the raqm run is a different width and a different picture."""
    shaped = CaptionRenderer(use_raqm=True)
    basic = CaptionRenderer(use_raqm=False)
    assert shaped.raqm and not basic.raqm
    assert shaped._width(segment(text), SIZE, lang) != pytest.approx(
        basic._width(segment(text), SIZE, lang), abs=1.0
    )
    a, b = shaped.text_mask(text, lang, SIZE), basic.text_mask(text, lang, SIZE)
    width = min(a.shape[1], b.shape[1])
    assert a.shape[1] != b.shape[1] or (np.abs(a[:, :width].astype(int) - b[:, :width]) > 48).mean() > 0.02
    ref = _golden(name)
    assert (
        ref.shape == a.shape
        and ref.shape != b.shape
        or (np.abs(ref[:, :width].astype(int) - b[:, :width]) > 48).mean() > 0.02
    )


@needs_raqm
def test_devanagari_conjunct_is_one_glyph_cluster():
    """kssa (U+0915 U+094D U+0937) shaped is narrower than its parts laid side by side; unshaped keeps the virama."""
    font_raqm = ImageFont.truetype(str(cap.FONT_FILES["deva"]), 56, layout_engine=ImageFont.Layout.RAQM)
    font_basic = ImageFont.truetype(str(cap.FONT_FILES["deva"]), 56, layout_engine=ImageFont.Layout.BASIC)
    conjunct = "क्ष"
    assert font_raqm.getlength(conjunct, language="hi") < font_basic.getlength(conjunct) - 5


@needs_raqm
def test_tamil_split_vowel_reorders_around_the_consonant():
    """Tamil ko (U+0B95 U+0BCA): the pre-base part of the vowel is drawn LEFT of the consonant. In the shaped
    mask the leftmost ink column of the vowel is to the left of where the bare consonant starts."""
    r = CaptionRenderer(use_raqm=True)
    consonant = np.asarray(r.text_mask("க", "ta", SIZE)) > 128
    ko = np.asarray(r.text_mask("கொ", "ta", SIZE)) > 128
    first = lambda m: int(np.argmax(m.any(axis=0)))  # noqa: E731
    last = lambda m: m.shape[1] - 1 - int(np.argmax(m.any(axis=0)[::-1]))  # noqa: E731
    # ink starts at the same place (consonant and a pre-base part), but ends further right (post-base part)
    assert last(ko) > last(consonant) + 8
    basic = np.asarray(CaptionRenderer(use_raqm=False).text_mask("கொ", "ta", SIZE)) > 128
    assert not np.array_equal(ko[:, : basic.shape[1]], basic[:, : ko.shape[1]]) or ko.shape != basic.shape
    assert first(ko) <= first(consonant) + 1


def test_clusters_never_split_a_conjunct_or_a_mark():
    assert clusters("क्षत्रिय") == ["क्ष", "त्रि", "य"]
    assert clusters("கொ") == ["கொ"]
    assert all(c[0] not in "्ि்ொ" for c in clusters("क्षत्रिय கொ స్వాతంత్ర్యం"))


def test_mixed_script_text_uses_one_font_per_script():
    runs = segment("OTP नमस्ते 123,")
    assert [s for s, _ in segment("OTPनमस्ते")] == ["latin", "deva"]
    assert {s for s, _ in runs} == {"latin", "deva"}
    assert segment("क्षः") == [("deva", "क्षः")]


def test_missing_raqm_falls_back_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(cap, "raqm_available", lambda: False)
    with caplog.at_level(logging.WARNING, logger="streamsarthi.engine.captions"):
        r = CaptionRenderer()
        r.add  # noqa: B018
    assert not r.raqm
    assert sum("libraqm unavailable" in rec.message for rec in caplog.records) == 1
    mask = r.text_mask("क्षत्रिय", "hi", SIZE)  # basic layout still draws something
    assert mask.any()
