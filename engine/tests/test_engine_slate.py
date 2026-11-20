"""Slates: English and Hindi text, raqm shaping, font provenance and the capture high-water-mark fence."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import ImageFont, features

from contracts import Config
from engine import slate as slate_mod
from engine.slate import SLATE_KINDS, SlatePresenter, SlateStore, hold_slate_kind, raqm_available, slate_texts

ASSETS = Path(slate_mod.__file__).parent / "assets"
needs_raqm = pytest.mark.skipif(not features.check("raqm"), reason="Pillow built without libraqm")


def test_font_files_match_the_recorded_hashes_and_ship_their_licences():
    notice = (ASSETS / "NOTICE.md").read_text(encoding="utf-8")
    recorded = set(re.findall(r"[0-9a-f]{64}", notice))
    for name in (
        "AtkinsonHyperlegibleNext-wght.ttf",
        "NotoSansDevanagari-VF.ttf",
        "OFL-AtkinsonHyperlegibleNext.txt",
        "OFL-NotoSansDevanagari.txt",
    ):
        digest = hashlib.sha256((ASSETS / name).read_bytes()).hexdigest()
        assert digest in recorded, f"{name} hash not recorded in NOTICE.md"
    for ofl in ("OFL-AtkinsonHyperlegibleNext.txt", "OFL-NotoSansDevanagari.txt"):
        assert "SIL OPEN FONT LICENSE Version 1.1" in (ASSETS / ofl).read_text(encoding="utf-8")


@needs_raqm
def test_raqm_shapes_devanagari_conjuncts():
    assert raqm_available()
    text = "क्षत्रिय निजता सुरक्षा चालू"
    shaped = ImageFont.truetype(str(slate_mod.DEVANAGARI_FONT), 56, layout_engine=ImageFont.Layout.RAQM)
    basic = ImageFont.truetype(str(slate_mod.DEVANAGARI_FONT), 56, layout_engine=ImageFont.Layout.BASIC)
    assert shaped.getlength(text, language="hi") != basic.getlength(
        text
    )  # conjuncts and matras form ligatures


def test_four_slates_are_opaque_distinct_and_read_only():
    store = SlateStore()
    images = [store.get(kind) for kind in SLATE_KINDS]
    for image in images:
        assert image.shape == (720, 1280, 3) and image.dtype == np.uint8 and not image.flags.writeable
    assert len({hashlib.sha256(i.tobytes()).hexdigest() for i in images}) == 4
    # dark field, with light text and a coloured mark on it
    assert all(30 < float(i.mean()) < 60 for i in images) or all(float(i.mean()) < 60 for i in images)
    assert all(int(i.max()) > 200 for i in images)


def test_hold_slate_carries_the_configured_english_and_hindi_text():
    a = SlateStore(Config(_env_file=None))
    b = SlateStore(
        Config(_env_file=None, hold={"slate_text_en": "Stream paused", "slate_text_hi": "स्ट्रीम रुकी है"})
    )
    assert slate_texts(Config(_env_file=None))["HOLD"].headline_en == "Privacy hold"
    assert "निजता" in slate_texts(Config(_env_file=None))["HOLD"].headline_hi
    ha, hb = a.get("HOLD"), b.get("HOLD")
    assert not np.array_equal(ha, hb)
    # the two text lines sit in bands: English headline near row 205, Hindi near row 290
    band_en, band_hi = (slice(200, 280), slice(0, 1280)), (slice(285, 380), slice(0, 1280))
    assert not np.array_equal(ha[band_en], hb[band_en])
    assert not np.array_equal(ha[band_hi], hb[band_hi])
    # other kinds are not affected by the hold copy
    assert np.array_equal(a.get("PROTECT"), b.get("PROTECT"))


def test_every_slate_has_a_hindi_line():
    texts = slate_texts()
    assert set(texts) == set(SLATE_KINDS)
    for text in texts.values():
        assert any("ऀ" <= ch <= "ॿ" for ch in text.headline_hi + text.sub_hi)
        assert text.headline_en and text.sub_en
    assert texts["PROTECT"].headline_en == "Checking privacy coverage"


@needs_raqm
def test_raqm_and_basic_layout_render_conjuncts_differently():
    cfg = Config(_env_file=None, hold={"slate_text_hi": "क्षत्रिय श्रद्धा"})
    shaped = SlateStore(cfg, use_raqm=True).get("HOLD")
    basic = SlateStore(cfg, use_raqm=False).get("HOLD")
    hindi_band = (slice(285, 380), slice(0, 1280))
    assert not np.array_equal(shaped[hindi_band], basic[hindi_band])


def test_missing_raqm_falls_back_and_logs_once(caplog):
    with caplog.at_level(logging.WARNING, logger="streamsarthi.engine.slate"):
        store = SlateStore(use_raqm=False)
    assert any("libraqm unavailable" in r.message for r in caplog.records)
    assert store.get("HOLD").shape == (720, 1280, 3)


def test_scaled_canvas_renders():
    small = SlateStore(width=640, height=360)
    assert small.get("PROTECT").shape == (360, 640, 3)
    with pytest.raises(ValueError):
        SlateStore(width=100, height=50)


def test_hold_slate_kind_mapping():
    assert hold_slate_kind("hotkey") == "HOLD"
    assert hold_slate_kind("api") == "HOLD"
    assert hold_slate_kind("auto_transport") == "TRANSPORT"
    assert hold_slate_kind("auto_egress") == "TRANSPORT"
    assert hold_slate_kind("hold-fence", "TRANSPORT") == "TRANSPORT"  # fence keeps what is on screen
    assert hold_slate_kind("retro-hold", "HOLD") == "HOLD"


# ------------------------------------------------------------------ the fence


@pytest.fixture(scope="module")
def store() -> SlateStore:
    return SlateStore()


def test_fence_blocks_frames_up_to_the_high_water_mark_after_a_hold(store):
    p = SlatePresenter(store)
    assert p.present("LIVE", "covered", 5, 20) is None
    assert p.present("HOLD", "hotkey", 6, 40) == "HOLD"
    assert p.present("LIVE", "covered", 39, 41) == "HOLD"  # captured before the mark
    assert p.present("LIVE", "covered", 40, 41) == "HOLD"
    assert p.present("LIVE", "covered", 41, 41) is None  # first frame newer than the mark
    assert p.fenced_frames == 2


def test_derived_hold_reasons_do_not_move_the_mark(store):
    p = SlatePresenter(store)
    p.present("HOLD", "api", 1, 50)
    p.present("HOLD", "retro-hold", 2, 90)  # slated because of the retro window, not an active hold
    p.present("HOLD", "hold-fence", 3, 95)
    assert p.blocked_through == 50
    assert p.present("LIVE", "covered", 51, 95) is None


def test_active_hold_keeps_moving_the_mark_and_transport_keeps_its_slate(store):
    p = SlatePresenter(store)
    assert p.present("HOLD", "auto_transport", 1, 10) == "TRANSPORT"
    assert p.present("HOLD", "auto_transport", 2, 30) == "TRANSPORT"
    assert p.blocked_through == 30
    assert p.present("LIVE", "covered", 25, 31) == "TRANSPORT"  # the fence shows the slate already on screen


def test_protect_fence_is_opt_in_and_reset_clears_it(store):
    plain = SlatePresenter(store)
    assert plain.present("PROTECT", "missing-fast", 1, 99) == "PROTECT"
    assert plain.present("LIVE", "covered", 2, 99) is None
    strict = SlatePresenter(store, fence_on_protect=True)
    strict.present("PROTECT", "missing-fast", 1, 99)
    assert strict.present("LIVE", "covered", 50, 99) == "HOLD"
    strict.reset()
    assert strict.present("LIVE", "covered", 50, 99) is None
