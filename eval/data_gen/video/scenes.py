"""Text-free negative scenes (``neg_scene``): natural-looking scenes and UI screenshots with no text at all.

The signboard negative of Gate 2: the text detector's ``mask_all_text`` rule masks every text-like box by design on
documents and screens that contain text, so a scene that holds no text is where a ``signboard`` mask is a false mask.
Even indices are scenes (textured backdrops with plain shapes: street, wall, desk, cloth, wood, dark room), odd
indices are UI screenshots made of rounded cards, avatars, colour bars, toggles and icon-like shapes, never a glyph.
Nothing is a target; the whole scene is one distractor (``non_text_scene``): every mask on these frames is a false mask.
"""

from __future__ import annotations

import random

import cv2
import numpy as np

from ...sender.stamp import STAMP_EXCLUSION
from .compose import BACKDROP_STYLES, CANVAS, Sprite, SpriteTarget, backdrop, rect_quad


def _ui(g: np.random.Generator, w: int, h: int) -> np.ndarray:
    dark = bool(g.random() < 0.5)
    bg = np.array([22, 24, 32] if dark else [236, 238, 242], np.uint8)
    img = np.full((h, w, 3), bg, np.uint8)
    accent = tuple(int(c) for c in g.integers(60, 230, 3))
    img[:64] = (np.array(accent) * (0.5 if dark else 0.9)).astype(np.uint8)  # app bar
    cv2.circle(img, (40, 32), 14, (245, 245, 245), -1, cv2.LINE_AA)  # menu dot
    cols, rows = int(g.integers(2, 4)), int(g.integers(2, 4))
    pad = 28
    cw, ch = (w - pad * (cols + 1)) // cols, (h - 64 - pad * (rows + 1)) // rows
    for r in range(rows):
        for c in range(cols):
            x, y = pad + c * (cw + pad), 64 + pad + r * (ch + pad)
            card = np.array([40, 44, 56] if dark else [255, 255, 255], np.uint8)
            cv2.rectangle(img, (x, y), (x + cw, y + ch), tuple(int(v) for v in card), -1, cv2.LINE_AA)
            col = tuple(int(v) for v in g.integers(40, 235, 3))
            cv2.circle(img, (x + 46, y + 46), 26, col, -1, cv2.LINE_AA)  # avatar
            cv2.rectangle(img, (x + 90, y + 28), (x + cw - 30, y + 44), col, -1)  # colour bars, not text
            cv2.rectangle(img, (x + 90, y + 56), (x + int(cw * 0.6), y + 66), (150, 150, 155), -1)
            if g.random() < 0.6:
                cv2.rectangle(img, (x + 20, y + ch - 60), (x + cw - 20, y + ch - 20), col, 2, cv2.LINE_AA)
            if g.random() < 0.5:  # toggle
                cv2.rectangle(
                    img, (x + cw - 80, y + ch - 100), (x + cw - 30, y + ch - 80), (90, 200, 120), -1
                )
                cv2.circle(img, (x + cw - 40, y + ch - 90), 11, (250, 250, 250), -1, cv2.LINE_AA)
    return img


def render_neg_scene(rng: random.Random, index: int) -> Sprite:
    g = np.random.default_rng(rng.getrandbits(63))
    w, h = CANVAS
    if index % 2 == 0:
        style = BACKDROP_STYLES[int(g.integers(0, len(BACKDROP_STYLES)))]
        img = backdrop(g, style)
        kind = "scene"
    else:
        img = _ui(g, w, h)
        kind = "ui"
        style = "ui"
    # the whole scene is one distractor: nothing in it may be masked (it is text-free by construction)
    top = STAMP_EXCLUSION[3] + 2  # below the optical stamp strip, which no mask may touch
    dist = SpriteTarget("non_text_scene", rect_quad((0, top, w, h)), {"scene": kind})
    return Sprite(img, [], distractors=[dist], meta={"scene": kind, "style": style, "text": False})
