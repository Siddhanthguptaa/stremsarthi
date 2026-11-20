"""SPECIMEN card / QR generators and dataset determinism (spec 9 tests 3 and 4)."""

import json
import random

import numpy as np
import pytest
import zxingcpp

from eval.data_gen.common import specimen, verhoeff
from eval.data_gen.video import dataset
from eval.data_gen.video.cards import CARD_W, render_aadhaar, render_pan
from eval.data_gen.video.qr import render_qr_poster
from eval.data_gen.video.render import apply_watermark
from eval.sender.stamp import STAMP_EXCLUSION


def _decode(img: np.ndarray):
    return [r.text for r in zxingcpp.read_barcodes(img)]


@pytest.mark.parametrize("seed", range(5))
def test_aadhaar_card_has_valid_specimen_number_and_watermark(seed):
    card = render_aadhaar(random.Random(seed))
    digits = card.number.replace(" ", "")
    assert verhoeff.is_valid(digits) and digits[0] in "01" and card.number.count(" ") == 2
    assert card.fields["name"] == "SPECIMEN PERSON" and card.fields["qr_payload"] == "SPECIMEN"
    # watermark exists, is at least 8 % of the card width, and the digits box is not covered
    assert card.watermark.width_fraction >= specimen.MIN_WATERMARK_WIDTH_FRACTION
    plain = render_aadhaar(random.Random(seed), watermark=False)
    diff = np.any(card.image != plain.image, axis=2)
    assert diff.sum() > 2000
    x1, y1, x2, y2 = card.watermark.bbox
    outside = diff.copy()
    outside[y1:y2, x1:x2] = False
    assert outside.sum() == 0  # every changed pixel lies inside the reported watermark bounds
    nx1, ny1, nx2, ny2 = card.number_bbox
    assert not diff[ny1:ny2, nx1:nx2].any()  # digits region identical with and without the mark
    assert card.image.shape == (404, CARD_W, 3)
    # the QR on the card decodes to the word SPECIMEN
    assert "SPECIMEN" in _decode(card.image)


@pytest.mark.parametrize("seed", range(5))
def test_pan_card_fourth_char_x_and_watermark(seed):
    card = render_pan(random.Random(seed))
    assert card.number[3] == "X" and len(card.number) == 10
    assert card.watermark.width_fraction >= 0.08
    plain = render_pan(random.Random(seed), watermark=False)
    diff = np.any(card.image != plain.image, axis=2)
    assert diff.sum() > 2000
    nx1, ny1, nx2, ny2 = card.number_bbox
    assert not diff[ny1:ny2, nx1:nx2].any()


def test_cards_are_reproducible_and_differ_by_seed():
    a = render_aadhaar(random.Random(7))
    b = render_aadhaar(random.Random(7))
    c = render_aadhaar(random.Random(8))
    assert np.array_equal(a.image, b.image) and a.number == b.number
    assert a.number != c.number


def test_upi_qr_poster_decodes_to_specimen_upi_link():
    qr = render_qr_poster(random.Random(3))
    assert qr.positive and qr.payload.startswith("upi://pay?pa=zz-specimen-")
    assert qr.payload.endswith("@specimen&pn=SPECIMEN%20TEST&am=1&cu=INR")
    assert qr.upi_id is not None and qr.upi_id.endswith("@specimen")
    assert _decode(qr.image) == [qr.payload]
    assert qr.watermark.width_fraction >= 0.08


def test_qr_negative_payloads_decode_and_are_not_upi():
    for payload in specimen.QR_NEGATIVE_PAYLOADS:
        qr = render_qr_poster(random.Random(4), payload=payload)
        assert not qr.positive and _decode(qr.image) == [payload]


def test_real_upi_handles_still_specimen_prefixed():
    qr = render_qr_poster(random.Random(5), handle="oksbi")
    assert "pa=zz-specimen-" in qr.payload and "@oksbi&" in qr.payload


def test_watermark_minimum_width_enforced():
    from PIL import Image

    with pytest.raises(ValueError):
        apply_watermark(Image.new("RGB", (400, 300)), width_fraction=0.05)


def test_dataset_deterministic_and_seed_sensitive(tmp_path):
    a = dataset.build_dataset(tmp_path / "a", 1234, n_per_kind=2)
    b = dataset.build_dataset(tmp_path / "b", 1234, n_per_kind=2)
    c = dataset.build_dataset(tmp_path / "c", 99, n_per_kind=2)
    assert a["manifest_sha256"] == b["manifest_sha256"] != c["manifest_sha256"]
    assert [i["pixel_sha256"] for i in a["items"]] == [i["pixel_sha256"] for i in b["items"]]
    on_disk = json.loads((tmp_path / "a" / "manifest.json").read_text())
    assert on_disk["seed"] == 1234 and on_disk["generator_version"] == dataset.GENERATOR_VERSION
    assert len(on_disk["items"]) == 8 and (tmp_path / "a" / "aadhaar_0000.png").exists()


def test_dataset_items_have_truth_and_avoid_stamp_region():
    m = dataset.build_dataset(None, 1234, n_per_kind=3)
    sx1, sy1, sx2, sy2 = STAMP_EXCLUSION
    kinds = {i["truth"]["category"] for i in m["items"]}
    assert kinds == set(dataset.KINDS)
    for item in m["items"]:
        quad = item["truth"]["quad"]
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        assert min(xs) >= 0 and max(xs) <= 1280 and min(ys) >= 0 and max(ys) <= 720
        assert max(xs) < sx1 or min(xs) > sx2 or max(ys) < sy1 or min(ys) > sy2
        assert item["truth"]["specimen"] is True


def test_dataset_images_decode_back_to_truth(tmp_path):
    from PIL import Image

    m = dataset.build_dataset(tmp_path, 1234, n_per_kind=2, kinds=("upi_qr", "qr_negative"))
    for item in m["items"]:
        img = np.asarray(Image.open(tmp_path / item["file"]).convert("RGB"))
        assert img.shape == (720, 1280, 3)
        texts = _decode(img)
        assert item["truth"]["payload"] in texts


def test_generator_output_passes_forbidden_pattern_scan():
    m = dataset.build_dataset(None, 2024, n_per_kind=20)
    assert specimen.scan_obj(m) == []
    # and a deliberately bad record is caught by the same scan
    body = "512345678901"[:11]
    bad = {"number": body + str(verhoeff.check_digit(body))}
    assert specimen.scan_obj(bad)
