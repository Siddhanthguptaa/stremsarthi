"""Sprites for SPECIMEN Aadhaar / PAN cards and their hard negatives (Verhoeff failures, plain cards, look-alikes).

Wraps the v0 renderers in ``cards.py``. Ground truth: one ``aadhaar`` / ``pan`` target (whole card quad) plus an
auxiliary ``*_number`` quad around the digits. Negatives return the card quad as a distractor so the harness
can check that nothing is masked there.
"""

from __future__ import annotations

import random
import re
import string

from PIL import Image, ImageDraw

from ..common import specimen, verhoeff
from .cards import CARD_H, CARD_W, CardRender, render_aadhaar, render_pan
from .compose import Sprite, SpriteTarget, rect_quad
from .render import apply_watermark, font, text_size, to_array


def _card_sprite(card: CardRender) -> Sprite:
    full = rect_quad((0, 0, CARD_W, CARD_H))
    attrs = {"number": card.number, "fields": card.fields}
    return Sprite(
        card.image,
        [
            SpriteTarget(card.kind, full, attrs),
            SpriteTarget(
                f"{card.kind}_number", rect_quad(card.number_bbox), {"aux": True, "number": card.number}
            ),
        ],
        meta={"card": card.kind},
    )


def aadhaar_sprite(rng: random.Random) -> Sprite:
    return _card_sprite(render_aadhaar(rng))


def pan_sprite(rng: random.Random) -> Sprite:
    return _card_sprite(render_pan(rng))


def non_verhoeff_digits(rng: random.Random, n: int = 12) -> str:
    """Random digit string of length `n` that fails the Verhoeff check."""
    while True:
        s = "".join(rng.choice(string.digits) for _ in range(n))
        if not verhoeff.is_valid(s):
            return s


def malformed_pan(rng: random.Random) -> str:
    """Ten characters that look like a PAN but fail ``[A-Z]{5}[0-9]{4}[A-Z]``."""
    letters = string.ascii_uppercase
    s = (
        "".join(rng.choice(letters) for _ in range(4))
        + "".join(rng.choice(string.digits) for _ in range(5))
        + rng.choice(letters)
    )
    assert not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", s)
    return s


def _plain_card(rng: random.Random, title: str, number_text: str | None) -> CardRender:
    bg = rng.choice(((236, 240, 246), (246, 240, 228), (226, 240, 232)))
    img = Image.new("RGB", (CARD_W, CARD_H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, CARD_W, 56), fill=rng.choice(((60, 80, 140), (140, 60, 60), (50, 120, 90))))
    d.text((16, 10), title, fill=(255, 255, 255), font=font(30))
    img, wm = apply_watermark(img, angle=22.0, width_fraction=0.6)
    d = ImageDraw.Draw(img)
    for i in range(3):  # decorative bars, no text
        d.rectangle((24, 100 + i * 40, 24 + rng.randint(160, 420), 116 + i * 40), fill=(200, 205, 215))
    box = (0, 0, 0, 0)
    if number_text:
        f = font(48)
        nw, nh = text_size(d, number_text, f)
        nx, ny = (CARD_W - nw) // 2, 280
        box = (nx - 12, ny - 8, nx + nw + 12, ny + nh + 18)
        d.rectangle(box, fill=(255, 255, 255))
        d.text((nx, ny), number_text, fill=(0, 0, 0), font=f)
    return CardRender(to_array(img), "other", number_text or "", box, wm, {})


def neg_card_sprite(rng: random.Random, subtype: str) -> Sprite:
    """Hard-negative card sprite. `subtype`: aadhaar_bad_checksum | pan_malformed | plain | membership."""
    if subtype == "aadhaar_bad_checksum":
        digits = non_verhoeff_digits(rng)
        card = render_aadhaar(rng, number_override=digits)
        note = "12 digits failing Verhoeff"
    elif subtype == "pan_malformed":
        card = render_pan(rng, number_override=malformed_pan(rng))
        note = "malformed PAN"
    elif subtype == "membership":
        while True:  # no 4-4-4 window may look like a real Aadhaar (the forbidden-pattern lint)
            n = non_verhoeff_digits(rng, 16)
            text = f"{n[0:4]} {n[4:8]} {n[8:12]} {n[12:16]}"
            if not specimen.find_forbidden(text):
                break
        card = _plain_card(rng, "SPECIMEN LIBRARY MEMBER", text)
        note = "16-digit membership number"
    else:
        card = _plain_card(rng, "SPECIMEN CARD", None)
        note = "plain card without number"
    full = rect_quad((0, 0, CARD_W, CARD_H))
    return Sprite(
        card.image,
        [],
        distractors=[
            SpriteTarget("card_non_pii", full, {"subtype": subtype, "note": note, "text": card.number})
        ],
        meta={"card": subtype},
    )
