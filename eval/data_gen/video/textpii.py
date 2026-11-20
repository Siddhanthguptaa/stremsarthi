"""On-screen text carrying phone numbers, PIN codes, UPI IDs and emails (and look-alike hard negatives).

Four layouts (document, chat, form, stream overlay) are drawn at 1280x720 scale so text stays legible at
distance 1.0. The PII text is fake: synthetic mobile numbers (all the forms from vision spec 5.9, including
+91 and Devanagari digits), PIN codes with cue words, ``zz-specimen-`` UPI IDs and ``.invalid`` emails.
Ground truth has one quad per PII string; ``distractors`` lists look-alikes that must not be masked.
"""

from __future__ import annotations

import random
from collections.abc import Callable

import numpy as np
from PIL import Image, ImageDraw

from ..common import identifiers, specimen, textlayout
from .compose import Sprite, SpriteTarget, rect_quad

W, H = 1280, 720

FILLER = (
    "Bhai stream ke baad milte hain, aaj ka game bahut mast tha",
    "Thanks for joining the live stream everyone",
    "Order will be delivered by tomorrow evening",
    "Please keep this message private and do not forward it",
    "Meeting notes: discuss the new overlay and schedule",
    "आज का स्ट्रीम बहुत मज़ेदार था, सबका धन्यवाद",
    "कल शाम सात बजे फिर मिलेंगे",
    "Remember to check the camera framing before going live",
    "New follower alert, welcome to the family",
    "Subscribe and turn on notifications for the next stream",
)
LEAD_IN = {
    "phone": ("Call me on", "Mera number hai", "WhatsApp", "Contact:", "फ़ोन:", "मेरा नंबर"),
    "upi_id": ("Pay to", "UPI ID:", "Send money to", "Donate here:", "UPI:"),
    "email": ("Email me at", "Mail:", "Business enquiries:", "Contact email:"),
    "pin": ("Address:", "Deliver to", "पता:", "Ship to:"),
}
ADDRESS_STUBS = (
    "Flat 12, SPECIMEN Residency, MG Road",
    "House 4, Sample Nagar, Ring Road",
    "Plot 7, Demo Colony",
)


def _bg_style(rng: random.Random) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """(page background, text colour, accent)."""
    return rng.choice(
        (
            ((250, 250, 248), (25, 25, 30), (40, 90, 170)),
            ((24, 26, 34), (235, 235, 240), (250, 190, 60)),
            ((240, 244, 250), (20, 30, 60), (30, 120, 90)),
            ((255, 246, 225), (60, 30, 20), (190, 60, 40)),
        )
    )


def _chrome(d: ImageDraw.ImageDraw, layout: str, accent) -> int:
    """Draw the layout chrome; returns the y where content starts."""
    if layout == "doc":
        d.rectangle((0, 0, W, 54), fill=accent)
        d.text(
            (24, 12), "NOTES - SPECIMEN", font=textlayout.load_font("latin", 26, 700), fill=(255, 255, 255)
        )
        return 90
    if layout == "chat":
        d.rectangle((0, 0, W, 64), fill=accent)
        d.text(
            (24, 14),
            "Chat - SPECIMEN group",
            font=textlayout.load_font("latin", 28, 700),
            fill=(255, 255, 255),
        )
        return 100
    if layout == "form":
        d.rectangle((0, 0, W, 70), fill=accent)
        d.text(
            (24, 16),
            "Order details - SPECIMEN STORE",
            font=textlayout.load_font("latin", 28, 700),
            fill=(255, 255, 255),
        )
        return 110
    return 60  # overlay


def _line_text(rng: random.Random, kind: str, value: str, pin_cue: str | None = None) -> str:
    if kind == "pin":
        return f"{rng.choice(ADDRESS_STUBS)}, {pin_cue or value}"
    return f"{rng.choice(LEAD_IN[kind])} {value}"


def render_text_screen(
    rng: random.Random, kind: str, layout: str | None = None, negatives_only: bool = False
) -> Sprite:
    """Screen with 1-2 PII strings of `kind` (phone | pin | upi_id | email), filler lines, and look-alikes.

    With ``negatives_only`` no PII is placed, only distractors and filler (hard-negative bucket).
    """
    layout = layout or rng.choice(("doc", "chat", "form", "overlay"))
    bg, fg, accent = _bg_style(rng)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    y = _chrome(d, layout, accent)
    size = rng.choice((30, 34, 38)) if layout != "overlay" else rng.choice((38, 42))
    line_h = int(size * 1.7)
    n_lines = (H - y - 40) // line_h
    n_pii = 0 if negatives_only else rng.choice((1, 1, 2))
    picks = rng.sample(range(n_lines), min(n_lines, n_pii + 2))
    pii_slots = set(picks[:n_pii])
    distractor_slots = set(picks[n_pii:])
    targets: list[SpriteTarget] = []
    distractors: list[SpriteTarget] = []
    margin_x = 40
    for i in range(n_lines):
        base = y + i * line_h + size
        bubble = layout == "chat"
        text: str
        value = ""
        form = ""
        is_pii = i in pii_slots
        is_dis = i in distractor_slots
        if is_pii:
            if kind == "phone":
                form = rng.choice(identifiers.PHONE_FORMS)
                value = identifiers.format_phone(specimen.phone_number(rng), form)
                text = _line_text(rng, "phone", value)
            elif kind == "upi_id":
                value = specimen.upi_id(rng, rng.choice(specimen.UPI_HANDLES))
                text = _line_text(rng, "upi_id", value)
            elif kind == "email":
                value = identifiers.email_address(rng)
                text = _line_text(rng, "email", value)
            else:
                pin = specimen.pin_code(rng)
                form = rng.choice(identifiers.PIN_CUES)
                if "पिन" in form and rng.random() < 0.5:
                    pin = identifiers.to_devanagari_digits(pin)
                value = form.format(pin=pin)
                text = _line_text(rng, "pin", value, pin_cue=value)
        elif is_dis:
            if kind == "phone":
                value = identifiers.non_mobile_number(rng)
                text = f"Reference no. {value}"
            elif kind == "pin":
                value = identifiers.non_pin_number(rng)
                text = f"Order id {value} confirmed"
            elif kind == "upi_id":
                value = identifiers.social_handle(rng)
                text = f"Follow {value} on social media"
            else:
                value = identifiers.social_handle(rng)
                text = f"Tag {value} in your post"
        else:
            text = rng.choice(FILLER)
        while textlayout.text_width(text, size) > W - 2 * margin_x - 60 and size > 22:
            size -= 2
        if bubble:
            tw = textlayout.text_width(text, size)
            bx1 = margin_x if i % 2 == 0 else W - margin_x - tw - 40
            d.rounded_rectangle(
                (bx1, base - size - 10, bx1 + tw + 40, base + 14),
                radius=18,
                fill=accent if i % 2 else (255, 255, 255) if bg[0] > 128 else (52, 56, 70),
            )
            x = bx1 + 20
            col = (255, 255, 255) if i % 2 else (fg if bg[0] <= 128 else (25, 25, 30))
        else:
            x = margin_x
            col = fg
        textlayout.draw_line(d, x, base, text, size, col)
        if is_pii or is_dis:
            box = textlayout.substring_box(text, value, x, base, size)
            if box:
                bx1, by1, bx2, by2 = box
                q = rect_quad((bx1 - 4, by1 - 2, bx2 + 4, by2 + 4))
                if is_pii:
                    targets.append(
                        SpriteTarget(f"text_{kind}", q, {"form": form, "cue": kind == "pin", "value": value})
                    )
                else:
                    distractors.append(
                        SpriteTarget(
                            {
                                "phone": "non_mobile_number",
                                "pin": "six_digit_no_cue",
                                "upi_id": "social_handle",
                                "email": "social_handle",
                            }[kind],
                            q,
                            {"value": value},
                        )
                    )
    d.text(
        (W - 24, H - 20),
        "SPECIMEN",
        font=textlayout.load_font("latin", 24, 700),
        fill=(200, 60, 60),
        anchor="rs",
    )
    return Sprite(
        np.asarray(img, dtype=np.uint8),
        targets,
        distractors=distractors,
        context=[SpriteTarget("screen", rect_quad((0, 0, W, H)), {})],
        meta={"layout": layout, "font_size": size, "kind": kind},
    )


Renderer = Callable[[random.Random], Sprite]
