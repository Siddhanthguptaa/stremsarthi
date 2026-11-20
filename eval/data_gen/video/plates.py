"""SPECIMEN Indian number plates on synthetic vehicle silhouettes.

Plates follow the standard format (``KA01AB1234``) and the Bharat series (``22BH1234AA``), one or two lines,
with the four common colour schemes. Vehicles are drawn from flat shapes (car, motorbike, truck rear views)
and carry a red "SPECIMEN" sticker outside the plate. Plate fonts are Noto Sans Bold (condensed), which only
approximates the real HSRP typeface; the blue IND strip is simplified (docs/specs/eval.md 5.1 rule 5).
"""

from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageDraw

from ..common import identifiers
from ..common.fonts import load_font
from .compose import Sprite, SpriteTarget, rect_quad

# colour scheme -> (background, text, border)
PLATE_SCHEMES = {
    "private": ((250, 250, 250), (12, 12, 12), (20, 20, 20)),
    "commercial": ((248, 205, 40), (12, 12, 12), (20, 20, 20)),
    "rental": ((18, 18, 18), (245, 205, 40), (245, 205, 40)),
    "electric": ((22, 140, 80), (250, 250, 250), (250, 250, 250)),
}
CAR_COLORS = (
    (200, 200, 205),
    (30, 30, 36),
    (170, 30, 30),
    (230, 230, 225),
    (40, 70, 130),
    (90, 90, 96),
    (160, 140, 100),
)


def render_plate(text: str, two_line: bool, scheme: str, height: int = 120) -> tuple[Image.Image, str]:
    """Plate artwork. Returns (image, layout) where layout is ``one_line`` or ``two_line``."""
    bg, fg, border = PLATE_SCHEMES[scheme]
    lines = (
        identifiers.plate_lines(text, two_line)
        if identifiers.PLATE_STANDARD_RE.fullmatch(text) or identifiers.PLATE_BH_RE.fullmatch(text)
        else [text]
    )
    two = len(lines) == 2
    h = height * (2 if two else 1) if not two else int(height * 1.75)
    w = int(height * (4.4 if not two else 2.4))
    img = Image.new("RGB", (w, h), border)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, w - 5, h - 5), radius=10, fill=bg)
    strip = int(w * 0.09)
    d.rounded_rectangle((4, 4, 4 + strip, h - 5), radius=10, fill=(24, 60, 150))
    d.rectangle((4 + strip // 2, 4, 4 + strip, h - 5), fill=(24, 60, 150))
    d.ellipse((12, h // 2 - 12, 12 + 24, h // 2 + 12), outline=(255, 255, 255), width=2)
    small = load_font("latin", max(10, strip // 3), 700)
    d.text((4 + strip // 2, h // 2 + 30), "IND", font=small, fill=(255, 255, 255), anchor="mm")
    avail_w = w - strip - 40
    size = int(h * (0.62 if not two else 0.36))
    while size > 14:
        f = load_font("latin", size, 700, 78)
        if max(f.getlength(t) for t in lines) <= avail_w:
            break
        size -= 2
    f = load_font("latin", size, 700, 78)
    cx = 4 + strip + (w - 4 - strip) / 2
    for i, t in enumerate(lines):
        cy = h * ((i + 0.5) / len(lines))
        d.text((cx, cy), t, font=f, fill=fg, anchor="mm")
    return img, "two_line" if two else "one_line"


def _plate_target(plate_img: Image.Image, px: int, py: int, attrs: dict) -> SpriteTarget:
    w, h = plate_img.size
    return SpriteTarget("plate", rect_quad((px, py, px + w, py + h)), attrs)


def _sticker(d: ImageDraw.ImageDraw, x: int, y: int, w: int = 200, h: int = 44) -> None:
    d.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=(200, 40, 40))
    d.text(
        (x + w / 2, y + h / 2),
        "SPECIMEN",
        font=load_font("latin", 28, 800),
        fill=(255, 255, 255),
        anchor="mm",
    )


def render_vehicle(
    rng: random.Random, text: str, kind: str, scheme: str | None = None, invalid_text: bool = False
) -> Sprite:
    """Vehicle rear view (RGBA sprite) carrying `text` on its plate.

    With ``invalid_text`` the plate text is not a registration (hard negative) and no plate target is emitted;
    the plate-shaped region is returned as a distractor instead.
    """
    two_line = kind == "bike" and not identifiers.PLATE_BH_RE.fullmatch(text) and not invalid_text
    if scheme is None:
        scheme = (
            "commercial" if kind == "truck" else rng.choices(list(PLATE_SCHEMES), weights=(6, 2, 1, 1))[0]
        )
    plate_img, layout = render_plate(text, two_line, scheme, 64 if kind != "truck" else 70)
    pw, ph = plate_img.size
    if kind == "car":
        W, H = 900, 640
        body = rng.choice(CAR_COLORS)
        img = Image.new("RGB", (W, H), (0, 0, 0))
        a = Image.new("L", (W, H), 0)
        d, da = ImageDraw.Draw(img), ImageDraw.Draw(a)
        for dd, fill in ((d, body), (da, 255)):
            dd.polygon(
                [(150, 230), (230, 60), (670, 60), (750, 230)],
                fill=fill if dd is da else tuple(int(c * 0.9) for c in body),
            )
            dd.rounded_rectangle((40, 210, 860, 520), radius=60, fill=fill)
        d.polygon([(190, 215), (250, 90), (650, 90), (710, 215)], fill=(30, 40, 52))  # rear glass
        for x0 in (52, 708):  # tail lights
            d.rounded_rectangle((x0, 250, x0 + 140, 330), radius=18, fill=(190, 20, 30))
        for dd, fill in ((d, (45, 45, 50)), (da, 255)):
            dd.rounded_rectangle((30, 470, 870, 570), radius=30, fill=fill)
            dd.ellipse((80, 540, 200, 640), fill=fill if dd is da else (15, 15, 15))
            dd.ellipse((700, 540, 820, 640), fill=fill if dd is da else (15, 15, 15))
        px, py = (W - pw) // 2, 395
        d.rounded_rectangle((px - 14, py - 10, px + pw + 14, py + ph + 10), radius=10, fill=(35, 35, 38))
        img.paste(plate_img, (px, py))
        _sticker(d, 560, 340, 200, 44) if rng.random() < 0.5 else _sticker(d, 150, 340, 200, 44)
    elif kind == "bike":
        W, H = 640, 760
        img = Image.new("RGB", (W, H), (0, 0, 0))
        a = Image.new("L", (W, H), 0)
        d, da = ImageDraw.Draw(img), ImageDraw.Draw(a)
        body = rng.choice(CAR_COLORS)
        for dd, fill in ((d, (25, 25, 28)), (da, 255)):
            dd.ellipse((170, 450, 470, 760), fill=fill)  # rear tyre
            dd.rounded_rectangle(
                (120, 60, 520, 290), radius=70, fill=fill if dd is da else body
            )  # tail section
            dd.polygon(
                [(170, 280), (470, 280), (500, 470), (140, 470)], fill=fill if dd is da else (40, 40, 44)
            )  # mudguard
        d.ellipse((260, 130, 380, 190), fill=(200, 30, 30))  # tail light
        px, py = (W - pw) // 2, 330
        d.rectangle((px - 8, py - 8, px + pw + 8, py + ph + 8), fill=(35, 35, 38))
        img.paste(plate_img, (px, py))
        _sticker(d, 220, 222, 200, 40)
    else:  # truck
        W, H = 1000, 640
        img = Image.new("RGB", (W, H), (0, 0, 0))
        a = Image.new("L", (W, H), 0)
        d, da = ImageDraw.Draw(img), ImageDraw.Draw(a)
        body = rng.choice(((70, 90, 70), (150, 60, 40), (60, 70, 110), (120, 120, 120)))
        for dd, fill in ((d, body), (da, 255)):
            dd.rectangle((30, 20, 970, 430), fill=fill)
            dd.rectangle((10, 430, 990, 500), fill=fill if dd is da else (50, 50, 55))
            dd.ellipse((90, 500, 250, 640), fill=fill if dd is da else (15, 15, 15))
            dd.ellipse((750, 500, 910, 640), fill=fill if dd is da else (15, 15, 15))
        for x0 in (60, 880):
            d.rectangle((x0, 360, x0 + 60, 410), fill=(200, 30, 30))
        px, py = (W - pw) // 2, 440
        d.rectangle((px - 10, py - 8, px + pw + 10, py + ph + 6), fill=(35, 35, 38))
        img.paste(plate_img, (px, py))
        _sticker(d, 70, 60, 220, 48)
    quad = rect_quad((px, py, px + pw, py + ph))
    attrs = {
        "text": text,
        "layout": layout,
        "scheme": scheme,
        "series": "bh" if identifiers.PLATE_BH_RE.fullmatch(text) else "standard",
        "vehicle": kind,
    }
    sprite = Sprite(
        np.asarray(img, dtype=np.uint8),
        [] if invalid_text else [SpriteTarget("plate", quad, attrs)],
        alpha=np.asarray(a, dtype=np.uint8),
        distractors=[SpriteTarget("non_plate_text", quad, {"text": text, "vehicle": kind})]
        if invalid_text
        else [],
        context=[SpriteTarget("vehicle", rect_quad((0, 0, W, H)), {"vehicle": kind})],
        meta={"vehicle": kind, "scheme": scheme},
    )
    return sprite
