"""Independent reference rasteriser for the caption shaping test: Pillow with libraqm on the Noto fonts in
``eval/fonts`` (own copies; the engine uses its own copies under ``engine/assets``), written without reading the
engine renderer. The engine mask must match the raqm reference and, where raqm and the basic layout differ, be
nearer to raqm (a renderer that fell back to the basic layout renders conjuncts and split vowels wrongly)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parent / "fonts"
FONT_FOR = {
    "hi": "NotoSansDevanagari-VF.ttf", "kn": "NotoSansKannada-VF.ttf", "ta": "NotoSansTamil-VF.ttf",
    "te": "NotoSansTelugu-VF.ttf", "bn": "NotoSansBengali-VF.ttf", "en": "NotoSans-VF.ttf",
}  # fmt: skip
SIZE = 36
WEIGHT = 500.0


def _font(lang: str, raqm: bool) -> ImageFont.FreeTypeFont:
    layout = ImageFont.Layout.RAQM if raqm else ImageFont.Layout.BASIC
    f = ImageFont.truetype(str(FONTS / FONT_FOR[lang]), SIZE, layout_engine=layout)
    try:
        vals = []
        for ax in f.get_variation_axes():
            name = ax["name"].decode() if isinstance(ax["name"], bytes) else str(ax["name"])
            want = {"Weight": WEIGHT, "Width": 100.0}.get(name, ax["default"])
            vals.append(min(max(want, ax["minimum"]), ax["maximum"]))
        f.set_variation_by_axes(vals)
    except (OSError, AttributeError, KeyError):
        pass
    return f


def ref_mask(text: str, lang: str, raqm: bool = True) -> np.ndarray:
    f = _font(lang, raqm)
    kw = {"language": lang} if raqm else {}
    w = int(np.ceil(f.getlength(text, **kw))) + 16
    asc, desc = f.getmetrics()
    im = Image.new("L", (w, asc + desc + 16), 0)
    ImageDraw.Draw(im).text((8, 8 + asc), text, font=f, fill=255, anchor="ls", **kw)
    return np.asarray(im, np.uint8).copy()


def ink_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask > 40)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _profile(mask: np.ndarray, width: int) -> np.ndarray:
    box = ink_box(mask)
    if box is None:
        return np.zeros(width)
    x0, _, x1, _ = box
    col = (mask[:, x0:x1] > 40).sum(axis=0).astype(np.float64)
    xs = np.linspace(0, len(col) - 1, width)
    return np.interp(xs, np.arange(len(col)), col)


def compare(engine_mask: np.ndarray, text: str, lang: str) -> dict[str, Any]:
    """Width error against the raqm reference and the basic layout, and the shape correlation with raqm."""
    r, b = ref_mask(text, lang, True), ref_mask(text, lang, False)
    be, br, bb = ink_box(engine_mask), ink_box(r), ink_box(b)
    if be is None or br is None or bb is None:
        return {"ok": False, "reason": "no ink", "ink": be is not None}
    we, wr, wb = be[2] - be[0], br[2] - br[0], bb[2] - bb[0]
    corr = float(np.corrcoef(_profile(engine_mask, 200), _profile(r, 200))[0, 1])
    corr_basic = float(np.corrcoef(_profile(engine_mask, 200), _profile(b, 200))[0, 1])
    differs = abs(wr - wb) / wr >= 0.02 or corr_basic < corr - 0.02
    width_err = abs(we - wr) / wr
    nearer = abs(we - wr) <= abs(we - wb) if abs(wr - wb) / wr >= 0.02 else corr >= corr_basic
    ok = width_err <= 0.04 and corr >= 0.85 and (nearer or not differs)
    return {
        "ok": bool(ok), "width_err": round(width_err, 4), "corr_raqm": round(corr, 4), "corr_basic": round(corr_basic, 4),
        "raqm_differs_from_basic": bool(differs), "width_engine": we, "width_raqm": wr, "width_basic": wb,
    }  # fmt: skip
