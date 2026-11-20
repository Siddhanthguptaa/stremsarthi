"""Identifier rules for the video generators (T2.1): Verhoeff, PAN, plates, phone/PIN/UPI/email, fonts."""

import random
import re

import pytest
from PIL import Image, ImageDraw

from eval.data_gen.common import fonts, identifiers, specimen, verhoeff
from eval.data_gen.video import idcards, signboards


@pytest.mark.parametrize("seed", range(200))
def test_plate_formats(seed):
    rng = random.Random(seed)
    std = identifiers.plate_standard(rng)
    bh = identifiers.plate_bh(rng)
    assert re.fullmatch(r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$", std)
    assert re.fullmatch(r"^\d{2}BH\d{4}[A-Z]{1,2}$", bh)
    assert std[:2] in identifiers.STATE_CODES and 21 <= int(bh[:2]) <= 30
    for text in (std, bh):
        one = identifiers.plate_lines(text, False)
        two = identifiers.plate_lines(text, True)
        assert len(one) == 1 and len(two) == 2
        assert "".join(one[0].split()) == text and "".join("".join(two).split()) == text


def test_invalid_plate_text_never_matches_a_plate_regex():
    for seed in range(300):
        t = "".join(identifiers.plate_invalid(random.Random(seed)).split())
        assert not identifiers.PLATE_STANDARD_RE.fullmatch(t) and not identifiers.PLATE_BH_RE.fullmatch(t)


def test_phone_forms_match_vision_regex_and_devanagari_roundtrip():
    rng = random.Random(1)
    for _ in range(200):
        number = specimen.phone_number(rng)
        for form in identifiers.PHONE_FORMS:
            text = identifiers.format_phone(number, form)
            ascii_text = identifiers.from_devanagari_digits(text)
            m = identifiers.PHONE_RE.fullmatch(ascii_text)
            assert m, (form, text)
            assert ascii_text.replace(" ", "").replace("-", "").replace("+", "")[-10:] == number
            assert ("devanagari" in form) == any(ch in identifiers.DEVANAGARI_DIGITS for ch in text)


def test_non_mobile_numbers_do_not_match_phone_regex():
    rng = random.Random(2)
    for _ in range(200):
        assert not identifiers.PHONE_RE.fullmatch(identifiers.non_mobile_number(rng))
        assert not identifiers.PIN_RE.fullmatch(identifiers.non_pin_number(rng))


def test_pin_cue_forms_contain_a_valid_pin():
    rng = random.Random(3)
    for cue in identifiers.PIN_CUES:
        pin = specimen.pin_code(rng)
        text = cue.format(pin=pin)
        assert identifiers.PIN_RE.search(text) and pin in text and pin[0] in "12345678"


def test_upi_email_and_handles():
    rng = random.Random(4)
    for _ in range(200):
        upi = specimen.upi_id(rng, rng.choice(specimen.UPI_HANDLES))
        assert identifiers.UPI_ID_RE.fullmatch(upi) and upi.startswith("zz-specimen-")
        mail = identifiers.email_address(rng)
        assert identifiers.EMAIL_RE.fullmatch(mail) and mail.endswith(".invalid")
        handle = identifiers.social_handle(rng)
        assert not identifiers.EMAIL_RE.fullmatch(handle) and not identifiers.UPI_ID_RE.fullmatch(handle)


def test_game_ids_and_otp_lengths():
    rng = random.Random(5)
    for _ in range(300):
        assert 8 <= len(identifiers.bgmi_player_id(rng)) <= 12
        assert 8 <= len(identifiers.freefire_uid(rng)) <= 12
        assert 4 <= len(identifiers.room_id(rng)) <= 8 and 4 <= len(identifiers.room_password(rng)) <= 8
        assert len(identifiers.otp_code(rng)) in (4, 6, 8)


def test_negative_card_numbers_fail_checks_and_pass_the_lint():
    rng = random.Random(6)
    for _ in range(100):
        digits = idcards.non_verhoeff_digits(rng)
        assert len(digits) == 12 and not verhoeff.is_valid(digits)
        pan = idcards.malformed_pan(rng)
        assert not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", pan)
    for sub in ("aadhaar_bad_checksum", "pan_malformed", "plain", "membership"):
        s = idcards.neg_card_sprite(random.Random(7), sub)
        assert not s.targets and s.distractors[0].category == "card_non_pii"
        assert not specimen.find_forbidden(str(s.distractors[0].attrs))


def test_fonts_are_pinned_and_raqm_shapes_indic_text():
    assert fonts.raqm_available()
    assert fonts.verify_fonts() == []
    assert (
        set(fonts.FONT_PINS)
        == set(fonts.SCRIPTS)
        == {"latin", "devanagari", "kannada", "tamil", "telugu", "bengali"}
    )
    assert all(len(p.sha256) == 64 for p in fonts.FONT_PINS.values())


@pytest.mark.parametrize("script", fonts.INDIC_SCRIPTS)
def test_every_signboard_phrase_has_glyphs_and_is_shaped(script):
    """No tofu (.notdef box) for any phrase character, and raqm output differs from unshaped basic layout."""
    f = fonts.load_font(script, 64)
    notdef = f.getmask("￿")  # unassigned code point renders the font's .notdef glyph
    notdef_bytes = bytes(notdef)
    for phrase in signboards.PHRASES[script]:
        for ch in set(phrase):
            if ch.isspace():
                continue
            m = f.getmask(ch)
            assert bytes(m) != notdef_bytes or m.size != notdef.size, (
                f"missing glyph for U+{ord(ch):04X} in {script}"
            )
    # shaping: a conjunct string must render differently under basic and raqm layout
    from PIL import ImageFont

    sample = " ".join(signboards.PHRASES[script])
    basic = ImageFont.truetype(str(fonts.FONT_PINS[script].path), 64, layout_engine=ImageFont.Layout.BASIC)
    assert f.getlength(sample) != basic.getlength(sample)


def test_signboard_targets_lie_inside_the_sprite_and_carry_script():
    for script in fonts.SCRIPTS:
        s = signboards.render_signboard(random.Random(11), script)
        h, w = s.image.shape[:2]
        assert s.targets and s.context[0].category == "signboard_board"
        for t in s.targets:
            xs, ys = [p[0] for p in t.quad], [p[1] for p in t.quad]
            assert min(xs) >= 0 and min(ys) >= 0 and max(xs) <= w and max(ys) <= h
            assert t.attrs["script"] in fonts.SCRIPTS
        assert s.targets[0].attrs["script"] == script
        img = Image.fromarray(s.image)
        assert ImageDraw.Draw(img) is not None
