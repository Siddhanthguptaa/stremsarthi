"""SPECIMEN signboards with text in Devanagari, Kannada, Tamil, Telugu, Bengali and Latin.

Text is shaped with raqm through Noto Sans (``common.fonts``). Every board carries a small "SPECIMEN" tag.
Targets are the individual text lines (the mask-all signboard detector masks every text box); the whole board
is stored as context. The phrases are ordinary shop and public-place words, no personal data.
"""

from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageDraw

from ..common.fonts import load_font
from .compose import Sprite, SpriteTarget, rect_quad

# Common signboard phrases per script (shop names, public places, greetings).
PHRASES: dict[str, list[str]] = {
    "devanagari": [
        "स्वागत है",
        "किराना स्टोर",
        "मिठाई की दुकान",
        "बस स्टैंड",
        "अस्पताल",
        "सब्जी मंडी",
        "पुलिस स्टेशन",
        "रेलवे स्टेशन",
        "शुद्ध घी भंडार",
        "गणेश जनरल स्टोर",
        "श्री राम होटल",
        "दवाइयों की दुकान",
    ],
    "kannada": [
        "ಸ್ವಾಗತ",
        "ಬಸ್ ನಿಲ್ದಾಣ",
        "ಆಸ್ಪತ್ರೆ",
        "ಕಿರಾಣಿ ಅಂಗಡಿ",
        "ತರಕಾರಿ ಮಾರುಕಟ್ಟೆ",
        "ಪೊಲೀಸ್ ಠಾಣೆ",
        "ರೈಲು ನಿಲ್ದಾಣ",
        "ಸಿಹಿ ತಿಂಡಿ",
        "ಶ್ರೀ ಕೃಷ್ಣ ಹೋಟೆಲ್",
        "ಔಷಧಿ ಅಂಗಡಿ",
    ],
    "tamil": [
        "வரவேற்கிறோம்",
        "பேருந்து நிலையம்",
        "மருத்துவமனை",
        "மளிகைக் கடை",
        "காய்கறி சந்தை",
        "காவல் நிலையம்",
        "ரயில் நிலையம்",
        "இனிப்பகம்",
        "உணவகம்",
        "மருந்தகம்",
    ],
    "telugu": [
        "స్వాగతం",
        "బస్ స్టాండ్",
        "ఆసుపత్రి",
        "కిరాణా దుకాణం",
        "కూరగాయల మార్కెట్",
        "పోలీస్ స్టేషన్",
        "రైల్వే స్టేషన్",
        "మిఠాయి దుకాణం",
        "శ్రీ వెంకటేశ్వర హోటల్",
        "మందుల దుకాణం",
    ],
    "bengali": [
        "স্বাগতম",
        "বাস স্ট্যান্ড",
        "হাসপাতাল",
        "মুদি দোকান",
        "সবজি বাজার",
        "থানা",
        "রেল স্টেশন",
        "মিষ্টির দোকান",
        "রেস্তোরাঁ",
        "ওষুধের দোকান",
    ],
    "latin": [
        "WELCOME",
        "GENERAL STORE",
        "BUS STAND",
        "CITY HOSPITAL",
        "VEGETABLE MARKET",
        "POLICE STATION",
        "RAILWAY STATION",
        "SWEET HOUSE",
        "NEW INDIA HOTEL",
        "MEDICAL STORE",
    ],
}
LATIN_SUBTITLES = ("GENERAL STORE", "BUS STAND", "HOSPITAL", "SWEETS", "RESTAURANT", "MEDICALS", "WELCOME")

# (background, text colour, border colour)
BOARD_STYLES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "shop_red": ((178, 38, 38), (255, 255, 255), (250, 210, 80)),
    "shop_blue": ((28, 70, 150), (255, 244, 200), (255, 255, 255)),
    "road_green": ((18, 110, 66), (255, 255, 255), (255, 255, 255)),
    "hospital": ((245, 245, 250), (20, 70, 150), (200, 40, 40)),
    "cream": ((238, 226, 190), (60, 30, 20), (120, 60, 30)),
    "dark": ((30, 30, 36), (250, 220, 90), (200, 200, 210)),
}


def _fit_font(script: str, text: str, max_w: int, start: int, weight: int):
    size = start
    while size > 24:
        f = load_font(script, size, weight)
        if f.getlength(text) <= max_w:
            return f
        size -= 4
    return load_font(script, 24, weight)


def render_signboard(rng: random.Random, script: str, with_specimen_tag: bool = True) -> Sprite:
    """One signboard sprite. ``script`` is the primary script; 50 % carry a Latin subtitle line."""
    style_name = rng.choice(list(BOARD_STYLES))
    bg, fg, border = BOARD_STYLES[style_name]
    w = rng.choice((900, 1000, 1100))
    lines: list[tuple[str, str]] = [(script, rng.choice(PHRASES[script]))]
    if script != "latin":
        if rng.random() < 0.4:
            second = rng.choice([p for p in PHRASES[script] if p != lines[0][1]])
            lines.append((script, second))
        if rng.random() < 0.5:
            lines.append(("latin", rng.choice(LATIN_SUBTITLES)))
    elif rng.random() < 0.6:
        lines.append(("latin", rng.choice([p for p in PHRASES["latin"] if p != lines[0][1]])))
    sizes = [118 if i == 0 else 84 for i in range(len(lines))]
    h = 120 + sum(int(s * 1.45) for s in sizes)
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, w - 11, h - 11), outline=border, width=8)
    for bx, by in ((34, 34), (w - 34, 34), (34, h - 34), (w - 34, h - 34)):  # bolts
        d.ellipse((bx - 7, by - 7, bx + 7, by + 7), fill=(170, 170, 175))
    targets: list[SpriteTarget] = []
    y = 60
    for i, ((sc, text), size) in enumerate(zip(lines, sizes, strict=True)):
        f = _fit_font(sc, text, w - 150, size, 700 if i == 0 else 600)
        y += int(f.size * 1.12)
        wid = f.getlength(text)
        x = (w - wid) / 2
        d.text((x, y), text, font=f, fill=fg, anchor="ls")
        x1, y1, x2, y2 = d.textbbox((x, y), text, font=f, anchor="ls")
        pad = 4
        targets.append(
            SpriteTarget(
                "signboard_text",
                rect_quad((x1 - pad, y1 - pad, x2 + pad, y2 + pad)),
                {"script": sc, "text": text, "line_index": i, "board_style": style_name},
            )
        )
        y += int(f.size * 0.33)
    if with_specimen_tag:
        tag = load_font("latin", 26, 700)
        d.text((w - 64, h - 26), "SPECIMEN", font=tag, fill=border, anchor="rs")
    return Sprite(
        np.asarray(img, dtype=np.uint8),
        targets,
        context=[SpriteTarget("signboard_board", rect_quad((0, 0, w, h)), {"script": script})],
        meta={"board_style": style_name, "script": script},
    )


def render_negative_board(rng: random.Random) -> Sprite:
    """Hard negative: board-like rectangle with only shapes, arrows and pictograms (no text)."""
    bg, fg, border = BOARD_STYLES[rng.choice(list(BOARD_STYLES))]
    w, h = 1000, 380
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, w - 11, h - 11), outline=border, width=8)
    for i in range(rng.randint(2, 4)):  # simple pictograms: arrow, circle, triangle
        cx = 180 + i * 220
        kind = rng.choice(("arrow", "circle", "tri"))
        if kind == "arrow":
            d.polygon(
                [
                    (cx - 60, 170),
                    (cx + 20, 170),
                    (cx + 20, 120),
                    (cx + 90, 190),
                    (cx + 20, 260),
                    (cx + 20, 210),
                    (cx - 60, 210),
                ],
                fill=fg,
            )
        elif kind == "circle":
            d.ellipse((cx - 70, 120, cx + 70, 260), outline=fg, width=14)
        else:
            d.polygon([(cx, 110), (cx + 80, 260), (cx - 80, 260)], outline=fg, width=14)
    return Sprite(
        np.asarray(img, dtype=np.uint8),
        [],
        context=[SpriteTarget("signboard_board", rect_quad((0, 0, w, h)), {})],
    )
