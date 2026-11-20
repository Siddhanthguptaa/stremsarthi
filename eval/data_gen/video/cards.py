"""SPECIMEN Aadhaar and PAN card renderers (v0).

Rules (docs/specs/eval.md 5.1): visible diagonal SPECIMEN watermark (>= 8 % of the card width) drawn on
the card background and never over the digits; Aadhaar numbers are Verhoeff-valid with a leading 0 or 1;
PAN 4th character is X; synthetic silhouette instead of a photo; QR payload is the word SPECIMEN.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from ..common import specimen
from .qr import qr_image
from .render import Rect, Watermark, apply_watermark, font, silhouette, text_size, to_array

CARD_W, CARD_H = 640, 404  # 85.6 x 54 mm ratio


@dataclass(frozen=True)
class CardRender:
    image: np.ndarray  # RGB uint8, CARD_H x CARD_W x 3
    kind: str  # "aadhaar" | "pan"
    number: str  # the digits / PAN exactly as drawn (Aadhaar formatted XXXX XXXX XXXX)
    number_bbox: Rect  # opaque rectangle holding the number, never covered by the watermark
    watermark: Watermark
    fields: dict[str, str]


def _name_block(d: ImageDraw.ImageDraw, x: int, y: int, lines: list[str], size: int = 22) -> None:
    f = font(size)
    for i, line in enumerate(lines):
        d.text((x, y + i * (size + 8)), line, fill=(30, 30, 30), font=f)


def render_aadhaar(
    rng: random.Random, watermark: bool = True, number_override: str | None = None
) -> CardRender:
    """SPECIMEN Aadhaar card. `number_override` (12 digits) is for hard negatives that fail Verhoeff."""
    raw = number_override or specimen.aadhaar_number(rng)
    number = specimen.format_aadhaar(raw)
    img = Image.new("RGB", (CARD_W, CARD_H), (246, 244, 236))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, CARD_W, 52), fill=(235, 130, 40))
    d.rectangle((0, CARD_H - 14, CARD_W, CARD_H), fill=(40, 140, 70))
    d.text((16, 8), "AADHAAR  -  SPECIMEN (SYNTHETIC)", fill=(255, 255, 255), font=font(30))
    wm = Watermark((0, 0, 0, 0), 0.0)
    if watermark:
        img, wm = apply_watermark(img, angle=22.0, width_fraction=0.6)
        d = ImageDraw.Draw(img)
    silhouette(d, (24, 76, 164, 236))
    dob = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1970, 2005)}"
    _name_block(d, 184, 80, ["SPECIMEN PERSON", f"DOB: {dob}", "SPECIMEN ADDRESS LINE"])
    qr = qr_image("SPECIMEN", 150)
    img.paste(qr, (CARD_W - 150 - 24, 72))
    f_num = font(60)
    nw, nh = text_size(d, number, f_num)
    nx, ny = (CARD_W - nw) // 2, 290
    box = (nx - 14, ny - 8, nx + nw + 14, ny + nh + 22)
    d.rectangle(box, fill=(255, 255, 255))
    d.text((nx, ny), number, fill=(0, 0, 0), font=f_num)
    return CardRender(
        to_array(img),
        "aadhaar",
        number,
        box,
        wm,
        {"name": "SPECIMEN PERSON", "dob": dob, "qr_payload": "SPECIMEN"},
    )


def render_pan(rng: random.Random, watermark: bool = True, number_override: str | None = None) -> CardRender:
    """SPECIMEN PAN card. `number_override` is for hard negatives with a malformed PAN."""
    pan = number_override or specimen.pan_number(rng)
    img = Image.new("RGB", (CARD_W, CARD_H), (214, 226, 240))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, CARD_W, 52), fill=(36, 64, 120))
    d.text((16, 8), "PERMANENT ACCOUNT NUMBER - SPECIMEN", fill=(255, 255, 255), font=font(28))
    wm = Watermark((0, 0, 0, 0), 0.0)
    if watermark:
        img, wm = apply_watermark(img, angle=22.0, width_fraction=0.6)
        d = ImageDraw.Draw(img)
    f_num = font(56)
    nw, nh = text_size(d, pan, f_num)
    nx, ny = 200, 96
    box = (nx - 12, ny - 8, nx + nw + 12, ny + nh + 20)
    d.rectangle(box, fill=(255, 255, 255))
    d.text((nx, ny), pan, fill=(0, 0, 0), font=f_num)
    silhouette(d, (24, 84, 164, 244))
    dob = f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1970, 2005)}"
    _name_block(d, 200, 200, ["SPECIMEN PERSON", "SPECIMEN PARENT", f"DOB: {dob}"], size=24)
    d.text((24, CARD_H - 40), "SPECIMEN - NOT A VALID DOCUMENT", fill=(120, 30, 30), font=font(22))
    return CardRender(
        to_array(img),
        "pan",
        pan,
        box,
        wm,
        {"name": "SPECIMEN PERSON", "parent": "SPECIMEN PARENT", "dob": dob},
    )
