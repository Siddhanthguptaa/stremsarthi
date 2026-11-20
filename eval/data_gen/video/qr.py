"""QR rendering and the UPI QR SPECIMEN poster.

QR encoding uses OpenCV's QRCodeEncoder (already a dependency), decode checks use zxing-cpp in tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..common import specimen
from .render import Rect, Watermark, apply_watermark, font, text_size, to_array


def qr_modules(payload: str) -> np.ndarray:
    """Boolean module matrix (True = dark) for `payload`, without quiet zone."""
    enc = cv2.QRCodeEncoder_create()
    img = enc.encode(payload)
    if img is None or img.size == 0:
        raise ValueError("QR encode failed")
    # OpenCV returns a bitmap with a 1-module border at scale 1..; find the dark bounding box.
    dark = img < 128
    ys, xs = np.nonzero(dark)
    crop = dark[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    # Estimate module size from the finder pattern (7 modules wide) along the first dark row.
    row = crop[0]
    run = 0
    for v in row:
        if v:
            run += 1
        else:
            break
    module = max(1, run // 7)
    n = crop.shape[0] // module
    return crop[: n * module : module, : n * module : module]


def qr_image(payload: str, box: int, quiet: int = 4) -> Image.Image:
    """Render `payload` as a black-on-white QR, `box` px square (nearest-neighbour, quiet zone included)."""
    mods = qr_modules(payload)
    n = mods.shape[0] + 2 * quiet
    padded = np.zeros((n, n), dtype=bool)
    padded[quiet:-quiet, quiet:-quiet] = mods
    scale = max(1, box // n)
    arr = np.where(np.kron(padded, np.ones((scale, scale), dtype=bool)), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr).convert("RGB")
    if img.size[0] != box:
        canvas = Image.new("RGB", (box, box), (255, 255, 255))
        off = (box - img.size[0]) // 2
        canvas.paste(img, (off, off))
        img = canvas
    return img


@dataclass(frozen=True)
class QrRender:
    image: np.ndarray  # RGB uint8
    payload: str
    upi_id: str | None
    positive: bool  # payload is a upi:// payment link
    qr_bbox: Rect
    watermark: Watermark


def render_qr_poster(rng: random.Random, payload: str | None = None, handle: str | None = None) -> QrRender:
    """Desk poster: 'SCAN TO PAY (SPECIMEN)' with a QR and, for UPI, the SPECIMEN UPI id below.

    With `payload=None` a positive UPI payload is generated (``pa=zz-specimen-<hex>@<handle>``).
    """
    upi = None
    if payload is None:
        upi = specimen.upi_id(rng, handle or specimen.UPI_FAKE_HANDLE)
        payload = specimen.upi_payload(upi)
    positive = payload.startswith("upi://")
    w, h = 480, 600
    img = Image.new("RGB", (w, h), (250, 248, 240))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, 56), fill=(52, 76, 140))
    f_title = font(30)
    title = "SCAN TO PAY (SPECIMEN)"
    tw, th = text_size(d, title, f_title)
    d.text(((w - tw) // 2, (56 - th) // 2 - 4), title, fill=(255, 255, 255), font=f_title)
    img, wm = apply_watermark(img, angle=35.0, width_fraction=0.55)
    d = ImageDraw.Draw(img)
    qr_box = 380
    qx, qy = (w - qr_box) // 2, 84
    img.paste(qr_image(payload, qr_box), (qx, qy))
    f_id = font(24)
    label = upi if upi else "SPECIMEN QR"
    lw, lh = text_size(d, label, f_id)
    d.rectangle(((w - lw) // 2 - 8, 490, (w + lw) // 2 + 8, 490 + lh + 14), fill=(255, 255, 255))
    d.text(((w - lw) // 2, 494), label, fill=(0, 0, 0), font=f_id)
    f_s = font(22)
    d.text((24, 560), "SPECIMEN - SYNTHETIC TEST DATA", fill=(120, 30, 30), font=f_s)
    return QrRender(to_array(img), payload, upi, positive, (qx, qy, qx + qr_box, qy + qr_box), wm)
