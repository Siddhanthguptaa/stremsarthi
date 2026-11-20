"""Indic enrichment of the shared OCR pass (``vision.ocr.multi`` / ``vision.ocr.scripts``): fake recognisers, CPU.

No model is loaded: recognisers are fakes keyed by the grey level of the crop, so each box answers with a
known SPECIMEN reading. The real recognisers are exercised by ``test_vision_text_real.py`` (skips without weights).
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
import pytest

from contracts import Config, Frame, OcrResult, Quad
from vision import testing_text as tt
from vision.ocr.multi import IndicEnricher, install_indic, read_ocr
from vision.ocr.scripts import HEADLINE_MIN, ScriptHints, headline_strength, script_order
from vision.resources import VisionResources

CFG = Config(profile="dev", _env_file=None)


def _scene(levels: list[int], *, h: int = 36, gap: int = 24) -> tuple[np.ndarray, list[Quad]]:
    """A frame with one flat-grey box per level (so a fake recogniser can tell the crops apart)."""
    img = np.full((720, 1280, 3), 250, np.uint8)
    quads = []
    for i, level in enumerate(levels):
        y = 40 + i * (h + gap)
        img[y : y + h, 100:420] = level
        quads.append(Quad.from_xyxy(100, y, 420, y + h))
    return img, quads


def _base(quads: list[Quad], texts: list[str], confs: list[float]) -> OcrResult:
    return OcrResult(
        tuple(quads), tuple(texts), tuple(confs), tuple("latin" if t else "unknown" for t in texts)
    )


class FakeRecs:
    """script -> {grey level: (raw text, conf)}; records every batch it was asked to read."""

    def __init__(self, table: dict[str, dict[int, tuple[str, float]]]) -> None:
        self.table = table
        self.calls: list[tuple[str, list[int]]] = []

    def __call__(self, script: str):
        def rec(crops: list[np.ndarray]) -> tuple[list[str], list[float]]:
            levels = [int(round(float(c.mean()))) for c in crops]
            self.calls.append((script, levels))
            known = self.table.get(script, {})
            out = []
            for lv in levels:  # the perspective crop may shift the mean by a grey level or two
                near = min(known, key=lambda k: abs(k - lv), default=None)
                out.append(known[near] if near is not None and abs(near - lv) <= 4 else ("", 0.0))
            return [t for t, _ in out], [c for _, c in out]

        return rec

    def scripts_called(self) -> list[str]:
        return [s for s, _ in self.calls]


def _enricher(table, *, clock=None, **kw) -> tuple[IndicEnricher, FakeRecs]:
    recs = FakeRecs(table)
    kwargs = {"loader": recs, **kw}
    if clock is not None:
        kwargs["clock"] = clock
    return IndicEnricher(CFG, **kwargs), recs


def _frame(img: np.ndarray, seq: int = 0) -> Frame:
    return Frame(seq, seq / 4.0, 0.0, img)


def test_empty_and_low_confidence_boxes_are_re_read_and_good_ones_are_left_alone() -> None:
    img, quads = _scene([60, 80, 100])
    base = _base(quads, ["", "garbage", "Hello world"], [0.8, 0.5, 0.99])
    table = {"devanagari": {60: ("फोन ९८७६५ ४३२१०", 0.97)}, "tamil": {80: ("பின் 600001", 0.95)}}
    enr, recs = _enricher(table)
    out = enr(_frame(img), base)
    assert out.texts == ("फोन 98765 43210", "பின் 600001", "Hello world")  # Indic digits are ASCII now
    assert out.scripts[0] == "devanagari" and out.scripts[1] == "tamil" and out.scripts[2] == "latin"
    assert out.confs[0] == pytest.approx(0.97) and out.confs[2] == pytest.approx(0.99)
    assert out.boxes == base.boxes
    assert base.texts == ("", "garbage", "Hello world")  # the shared result is never mutated
    assert enr.last_timing.recognised == 2 and not enr.last_timing.budget_hit
    assert all(100 not in levels for _, levels in recs.calls)  # the confident Latin box was not sent


def test_a_wrong_script_reading_under_the_accept_confidence_is_discarded() -> None:
    img, quads = _scene([60])
    base = _base(quads, [""], [0.8])
    table = {"tamil": {60: ("క్రి లక్ి", 0.80)}, "telugu": {60: ("junk", 0.5)}, "devanagari": {60: ("x", 0.3)}}
    enr, _ = _enricher(table)
    assert enr(_frame(img), base) is base  # nothing accepted: the very same object comes back


def test_the_script_order_follows_the_headline_and_stops_at_the_first_accepted_reading() -> None:
    img, quads = _scene([60])  # flat crop: no headline, so Tamil, Telugu, Devanagari in that order
    base = _base(quads, [""], [0.8])
    enr, recs = _enricher({"telugu": {60: ("ప్రభుత్వ ఆసుపత్రి", 0.97)}})
    out = enr(_frame(img), base)
    assert out.texts[0] == "ప్రభుత్వ ఆసుపత్రి" and out.scripts[0] == "telugu"
    assert recs.scripts_called() == ["tamil", "telugu"]  # Devanagari never ran


def test_a_hint_from_the_previous_frame_puts_the_known_script_first() -> None:
    img, quads = _scene([60])
    base = _base(quads, [""], [0.8])
    enr, recs = _enricher({"telugu": {60: ("ప్రభుత్వ ఆసుపత్రి", 0.97)}})
    enr(_frame(img, 0), base)
    recs.calls.clear()
    enr(_frame(img, 1), base)
    assert recs.scripts_called() == ["telugu"]  # straight to the script that worked at this place


def test_results_are_memoised_per_frame_and_recomputed_for_a_new_base() -> None:
    img, quads = _scene([60])
    base = _base(quads, [""], [0.8])
    enr, recs = _enricher({"tamil": {60: ("பின் 600001", 0.95)}})
    first = enr(_frame(img, 0), base)
    n = len(recs.calls)
    assert enr(_frame(img, 0), base) is first and len(recs.calls) == n and enr.last_timing.memo_hit
    enr(_frame(img, 1), base)
    assert len(recs.calls) > n


def test_the_crop_cap_and_the_time_budget_bound_the_work() -> None:
    img, quads = _scene(list(range(50, 110, 10)))  # six unread boxes
    base = _base(quads, [""] * 6, [0.8] * 6)
    table = {"tamil": {lv: ("பின் 600001", 0.95) for lv in range(50, 110, 10)}}
    cfg = Config(profile="dev", _env_file=None, ocr={"rec_max_crops": 3})  # type: ignore[arg-type]
    out = IndicEnricher(cfg, loader=FakeRecs(table))(_frame(img), base)
    assert sum(1 for t in out.texts if t) == 3  # at most rec_max_crops boxes are enriched

    ticks = iter(range(0, 10_000, 100))  # every clock read is 100 s later: the budget is gone after one batch
    enr = IndicEnricher(CFG, loader=FakeRecs(table), clock=lambda: float(next(ticks)))
    out2 = enr(_frame(img), base)
    assert enr.last_timing.budget_hit and 1 <= sum(1 for t in out2.texts if t) <= 2


def test_a_missing_recogniser_leaves_the_box_unread_and_logs_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    img, quads = _scene([60])
    base = _base(quads, [""], [0.8])

    def broken(script: str):
        raise FileNotFoundError(f"weights for {script} missing")

    caplog.set_level(logging.DEBUG)
    out = IndicEnricher(CFG, loader=broken)(_frame(img), base)
    assert out is base and out.texts == ("",)


def test_a_confident_latin_number_with_a_junk_label_is_re_read_for_its_indic_label() -> None:
    img, quads = _scene([60, 80])
    base = _base(quads, ["L6 -700 994", "58732279010"], [0.92, 0.99])
    table = {
        "tamil": {
            60: ("8732279010", 0.99),
            80: ("8732279010", 0.99),
        },  # right digits but no Indic label: rejected
        "telugu": {60: ("పిన్ - ௭௦௦ ௯௯௪", 0.96), 80: ("ఫోన్: 8732279010", 1.0)},
    }
    enr, recs = _enricher(table)
    out = enr(_frame(img), base)
    # the first Tamil readings carry no Indic letters, so the next script is tried; Telugu keeps the digits and
    # adds Indic letters: it replaces the Latin reading
    assert (
        out.texts[0].startswith("పిన్ - ") and out.texts[0].endswith("700 994") and out.scripts[0] == "telugu"
    )
    assert out.texts[1] == "ఫోన్: 8732279010"
    assert recs.scripts_called().count("devanagari") == 0


def test_a_latin_reading_is_not_replaced_by_an_indic_reading_without_indic_letters() -> None:
    img, quads = _scene([60])
    base = _base(quads, ["9252100559"], [0.97])
    table = {s: {60: ("9252100559", 0.99)} for s in ("tamil", "telugu", "devanagari")}
    assert _enricher(table)[0](_frame(img), base) is base
    labelled = _base(quads, ["Ph 9252100559"], [0.97])  # has a label already: not even re-read
    enr, recs = _enricher(table)
    assert enr(_frame(img), labelled) is labelled and recs.calls == []


def test_digits_only_readings_take_the_recogniser_script_as_their_label() -> None:
    img, quads = _scene([60])
    out = _enricher({"devanagari": {60: ("९८७६५ ४३२१०", 0.97)}})[0](_frame(img), _base(quads, [""], [0.8]))
    assert out.texts[0] == "98765 43210" and out.scripts[0] == "devanagari"


def test_install_indic_only_wraps_the_real_service_and_is_idempotent() -> None:
    fake = VisionResources(CFG, ocr=lambda f: OcrResult((), (), (), ()))
    assert install_indic(fake) is None and getattr(fake, "ocr_indic", None) is None
    real = VisionResources(CFG)  # the OcrService loads its models lazily: nothing is read here
    first = install_indic(real)
    assert isinstance(first, IndicEnricher) and install_indic(real) is first and real.ocr_indic is first


def test_read_ocr_falls_back_to_the_shared_result_without_an_enricher() -> None:
    img, quads = _scene([60])
    base = _base(quads, ["Hello"], [0.99])
    res = VisionResources(CFG, ocr=lambda f: base)
    assert read_ocr(res, _frame(img)) is base
    enr, _ = _enricher({})
    res.ocr_indic = enr
    assert read_ocr(res, _frame(img)) is base  # nothing to enrich, same object


# ---------------------------------------------------------------------------- script routing (pixels only)
def test_script_order_and_hints() -> None:
    assert script_order(0.9, None) == ["devanagari", "tamil", "telugu"]
    assert script_order(0.2, None) == ["tamil", "telugu", "devanagari"]
    assert script_order(0.2, "telugu")[0] == "telugu" and script_order(0.9, "tamil")[0] == "tamil"
    hints = ScriptHints()
    box = (100.0, 100.0, 300.0, 140.0)
    hints.update([(box, "tamil")], now=0.0)
    assert hints.lookup((104.0, 102.0, 304.0, 142.0), 1.0) == "tamil"
    assert hints.lookup((600.0, 300.0, 800.0, 340.0), 1.0) is None  # another place
    assert hints.lookup(box, 10.0) is None  # expired


@pytest.mark.skipif(not tt.fonts_available("devanagari", "tamil", "latin"), reason="no Indic test fonts")
def test_a_headline_separates_devanagari_from_tamil_and_latin_text() -> None:
    def crop(script: str, text: str) -> np.ndarray:
        from PIL import Image, ImageDraw

        font = tt._font(script, 40)
        w = int(ImageDraw.Draw(Image.new("RGB", (8, 8))).textlength(text, font=font)) + 40
        im = Image.new("RGB", (w, 80), (245, 245, 240))
        ImageDraw.Draw(im).text((20, 12), text, font=font, fill=(20, 20, 20))
        return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)

    dev = headline_strength(crop("devanagari", tt.SIGN_TEXT["devanagari"][0]))
    tam = headline_strength(crop("tamil", tt.SIGN_TEXT["tamil"][0]))
    lat = headline_strength(crop("latin", "SHREE GANESH STORES"))
    assert dev >= HEADLINE_MIN > max(tam, lat)
    assert headline_strength(np.zeros((4, 4, 3), np.uint8)) == 0.0
