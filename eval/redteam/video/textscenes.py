"""Text-on-screen sprites for the font, mixed-script, split-line and separator attacks.

One synthetic document-like screen (SPECIMEN corner label, filler lines, one or more PII lines). The PII line is
drawn from ``Seg`` pieces so each attack controls the font, the script, the separators and where the line breaks;
each marked piece becomes a ground-truth target (``text_phone``, ``text_upi_id``, ...) with its ``value``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ...data_gen.common import fonts
from ...data_gen.video.compose import Sprite, SpriteTarget, rect_quad
from ...data_gen.video.textpii import FILLER

SW, SH = 1280, 720
SYSTEM_FONTS = {
    "dejavu_serif": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "dejavu_mono_oblique": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
    "ubuntu_thin": "/usr/share/fonts/truetype/ubuntu/Ubuntu-Th.ttf",
    "ubuntu_condensed": "/usr/share/fonts/truetype/ubuntu/Ubuntu-C.ttf",
    "ubuntu_light_italic": "/usr/share/fonts/truetype/ubuntu/Ubuntu-LI.ttf",
}
HERSHEY = {
    "hershey_script": cv2.FONT_HERSHEY_SCRIPT_COMPLEX,
    "hershey_script_simplex": cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
    "hershey_triplex": cv2.FONT_HERSHEY_TRIPLEX,
}


def available_fonts() -> list[str]:
    ok = [k for k, p in SYSTEM_FONTS.items() if Path(p).exists()]
    return ok + list(HERSHEY)


@dataclass(frozen=True)
class Seg:
    """A piece of the PII line. ``script`` selects a Noto face ("latin", "devanagari", "bengali", "tamil", ...);
    ``font`` (a SYSTEM_FONTS / HERSHEY key) overrides it. ``mark`` = (category, value) makes it a target."""

    text: str
    script: str = "latin"
    font: str | None = None
    mark: tuple[str, str] | None = None
    spacing: float = 0.0  # extra px between characters (letter-spaced digits)


def _pil_font(seg: Seg, size: int) -> ImageFont.FreeTypeFont:
    if seg.font in SYSTEM_FONTS:
        return ImageFont.truetype(SYSTEM_FONTS[seg.font], size, layout_engine=ImageFont.Layout.RAQM)
    return fonts.load_font(seg.script, size, 400)


def _seg_width(seg: Seg, size: int) -> float:
    if seg.font in HERSHEY:
        (w, _), _ = cv2.getTextSize(seg.text, HERSHEY[seg.font], size / 28.0, 2)
        return float(w)
    f = _pil_font(seg, size)
    if seg.spacing:
        return float(sum(f.getlength(c) + seg.spacing for c in seg.text))
    return float(f.getlength(seg.text))


def draw_line(
    img: Image.Image,
    x: float,
    base: float,
    segs: list[Seg],
    size: int,
    fg: tuple[int, int, int],
) -> tuple[Image.Image, list[SpriteTarget]]:
    """Draw ``segs`` left to right on one baseline. Returns the image and one target per marked segment."""
    d = ImageDraw.Draw(img)
    targets: list[SpriteTarget] = []
    cx = x
    for seg in segs:
        w = _seg_width(seg, size)
        if seg.font in HERSHEY:
            arr = np.asarray(img).copy()
            cv2.putText(
                arr, seg.text, (int(cx), int(base)), HERSHEY[seg.font], size / 28.0, fg, 2, cv2.LINE_AA
            )
            img = Image.fromarray(arr)
            d = ImageDraw.Draw(img)
            asc, desc = int(size * 0.95), int(size * 0.3)
        else:
            f = _pil_font(seg, size)
            if seg.spacing:
                px = cx
                for c in seg.text:
                    d.text((px, base), c, font=f, fill=fg, anchor="ls")
                    px += f.getlength(c) + seg.spacing
            else:
                d.text((cx, base), seg.text, font=f, fill=fg, anchor="ls")
            asc, desc = f.getmetrics()
        if seg.mark:
            cat, value = seg.mark
            q = rect_quad((cx - 4, base - asc - 2, cx + w + 4, base + desc + 4))
            targets.append(SpriteTarget(cat, q, {"value": value}))
        cx += w
    return img, targets


def screen(
    rng: random.Random,
    lines: list[tuple[list[Seg], int]],
    *,
    dark: bool | None = None,
    size_filler: int = 30,
) -> Sprite:
    """A screen with ``lines`` (segments, size) stacked in the middle and filler text around them."""
    dark = rng.random() < 0.4 if dark is None else dark
    bg = (24, 26, 34) if dark else (244, 244, 240)
    fg = (232, 232, 236) if dark else (24, 24, 30)
    img = Image.new("RGB", (SW, SH), bg)
    d = ImageDraw.Draw(img)
    f_fill = fonts.load_font("latin", size_filler, 400)
    n_top = 3
    y = 70
    for _ in range(n_top):
        d.text((60, y), rng.choice(FILLER), font=f_fill, fill=fg, anchor="ls")
        y += 56
    y += 40
    targets: list[SpriteTarget] = []
    for segs, size in lines:
        img, t = draw_line(img, 60, y, segs, size, fg)
        targets += t
        y += int(size * 1.9)
    d = ImageDraw.Draw(img)
    for _ in range(3):
        y += 56
        if y < SH - 60:
            d.text((60, y), rng.choice(FILLER), font=f_fill, fill=fg, anchor="ls")
    d.text(
        (SW - 24, SH - 20),
        "SPECIMEN",
        font=fonts.load_font("latin", 24, 700),
        fill=(200, 60, 60),
        anchor="rs",
    )
    return Sprite(
        np.asarray(img, dtype=np.uint8),
        targets,
        context=[SpriteTarget("screen", rect_quad((0, 0, SW, SH)), {})],
    )
