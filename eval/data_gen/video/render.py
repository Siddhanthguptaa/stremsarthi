"""Small shared drawing helpers for the SPECIMEN renderers (Pillow + numpy, CPU only)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..common.specimen import MIN_WATERMARK_WIDTH_FRACTION, SPECIMEN_WORD

Rect = tuple[int, int, int, int]  # x1, y1, x2, y2


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """The font bundled with Pillow (stable across machines, no system font dependency)."""
    return ImageFont.load_default(size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont | ImageFont.ImageFont):
    x1, y1, x2, y2 = draw.textbbox((0, 0), text, font=fnt)
    return x2 - x1, y2 - y1


@dataclass(frozen=True)
class Watermark:
    bbox: Rect  # where the watermark pixels live (axis-aligned bounds of the rotated text)
    width_fraction: float


def apply_watermark(
    img: Image.Image, angle: float = 25.0, color=(214, 60, 60), alpha: int = 150, width_fraction: float = 0.62
) -> tuple[Image.Image, Watermark]:
    """Draw a diagonal SPECIMEN watermark on `img` (RGB). Returns the new image and its bounds.

    The caller paints the digits / QR afterwards on opaque rectangles, so the watermark never covers
    anything a detector must read.
    """
    if width_fraction < MIN_WATERMARK_WIDTH_FRACTION:
        raise ValueError("watermark narrower than the SPECIMEN minimum")
    w, h = img.size
    target_w = int(w * width_fraction)
    size = 20
    while True:
        tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
        tw, _ = text_size(tmp, SPECIMEN_WORD, font(size))
        if tw >= target_w or size > 400:
            break
        size += 4
    fnt = font(size)
    tw, th = text_size(tmp, SPECIMEN_WORD, fnt)
    layer = Image.new("L", (tw + 20, th + 20), 0)
    ImageDraw.Draw(layer).text((10 - 0, 10 - 0), SPECIMEN_WORD, fill=255, font=fnt)
    layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    mask = layer.point(lambda v: min(alpha, v * alpha // 255))
    lw, lh = layer.size
    ox, oy = (w - lw) // 2, (h - lh) // 2
    out = img.convert("RGB").copy()
    out.paste(Image.new("RGB", layer.size, color), (ox, oy), mask)
    bbox = layer.getbbox() or (0, 0, lw, lh)
    wm_bbox = (max(0, ox + bbox[0]), max(0, oy + bbox[1]), min(w, ox + bbox[2]), min(h, oy + bbox[3]))
    return out, Watermark(wm_bbox, (wm_bbox[2] - wm_bbox[0]) / w)


def silhouette(draw: ImageDraw.ImageDraw, box: Rect, fill=(150, 150, 150), bg=(220, 220, 225)) -> None:
    """Synthetic head-and-shoulders silhouette in place of a photo."""
    x1, y1, x2, y2 = box
    draw.rectangle(box, fill=bg, outline=(120, 120, 120))
    w, h = x2 - x1, y2 - y1
    r = int(w * 0.22)
    cx = x1 + w // 2
    draw.ellipse((cx - r, y1 + int(h * 0.15), cx + r, y1 + int(h * 0.15) + 2 * r), fill=fill)
    draw.pieslice(
        (x1 + int(w * 0.12), y1 + int(h * 0.55), x2 - int(w * 0.12), y2 + int(h * 0.55)), 180, 360, fill=fill
    )


def to_array(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.uint8)
