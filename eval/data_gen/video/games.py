"""BGMI-style and Free-Fire-style lobby / custom-room overlays with fake player IDs, room IDs and passwords.

These are NOT copies of the games. They are our own synthetic UIs drawn at 1280x720 that approximate where
such games usually show a player ID (top-left profile card), a custom-room ID and a room password
(right-hand room panel), using generic labels and a "SPECIMEN UI" badge. Real layouts differ with game
version and device (UNVERIFIED), so the vision team needs calibration screenshots from a real phone before
tuning (docs/specs/vision.md 5.10). The layout of each style is fixed, as a template matcher expects.
"""

from __future__ import annotations

import random

import numpy as np
from PIL import Image, ImageDraw

from ..common import identifiers
from ..common.fonts import load_font
from .compose import Sprite, SpriteTarget, rect_quad

W, H = 1280, 720

STYLES = {
    # name: (bg top, bg bottom, accent, panel, text)
    "bgmi": ((16, 28, 44), (40, 56, 70), (245, 170, 30), (24, 36, 52), (235, 238, 242)),
    "freefire": ((34, 20, 12), (70, 38, 18), (255, 200, 40), (44, 26, 16), (255, 244, 225)),
}
NAMES = ("SPECIMEN_PLAYER", "TestUser_01", "DemoSquad", "SampleStreamer", "MockHero")


def _bg(style: str) -> Image.Image:
    top, bot, *_ = STYLES[style]
    ramp = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    arr = np.array(top, np.float32) * (1 - ramp) + np.array(bot, np.float32) * ramp
    img = Image.fromarray(np.broadcast_to(arr, (H, W, 3)).astype(np.uint8))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(6):  # diagonal decorative bands
        x = 120 + i * 230
        d.polygon([(x, 0), (x + 60, 0), (x - 140, H), (x - 200, H)], fill=(255, 255, 255, 10))
    return img


def _text_box(
    d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, weight: int, fill
) -> tuple[int, int, int, int]:
    f = load_font("latin", size, weight)
    d.text(xy, text, font=f, fill=fill, anchor="ls")
    x1, y1, x2, y2 = d.textbbox(xy, text, font=f, anchor="ls")
    return x1 - 5, y1 - 4, x2 + 5, y2 + 6


def render_lobby(rng: random.Random, style: str, with_credentials: bool = True) -> Sprite:
    """Custom-room lobby. `with_credentials=False` draws a loading / inventory screen with numbers instead."""
    bg_top, _bg_bot, accent, panel, fg = STYLES[style]
    img = _bg(style)
    d = ImageDraw.Draw(img, "RGBA")
    targets: list[SpriteTarget] = []
    distractors: list[SpriteTarget] = []
    # top bar with generic currency counters (digits that are NOT IDs)
    for i, label in enumerate(("DIAMONDS", "GOLD")):
        x = 820 + i * 220
        d.rounded_rectangle((x, 18, x + 200, 66), radius=14, fill=(0, 0, 0, 110))
        box = _text_box(d, (x + 16, 52), f"{rng.randint(10, 99999)}", 28, 700, fg)
        d.text((x + 196, 52), label[:3], font=load_font("latin", 14, 700), fill=accent, anchor="rs")
        distractors.append(SpriteTarget("currency_counter", rect_quad(box), {"label": label}))
    if with_credentials:
        pid = identifiers.bgmi_player_id(rng) if style == "bgmi" else identifiers.freefire_uid(rng)
        rid = identifiers.room_id(rng)
        pwd = identifiers.room_password(rng)
        # profile card, top-left (fixed position per style)
        d.rounded_rectangle((20, 16, 420, 110), radius=16, fill=panel + (230,))
        d.ellipse((32, 26, 96, 90), fill=accent)
        d.text((64, 58), "S", font=load_font("latin", 36, 800), fill=bg_top, anchor="mm")
        _text_box(d, (116, 52), rng.choice(NAMES), 26, 700, fg)
        label = "ID:" if style == "bgmi" else "UID:"
        d.text((116, 92), label, font=load_font("latin", 22, 600), fill=accent, anchor="ls")
        off = int(load_font("latin", 22, 600).getlength(label)) + 10
        pid_box = _text_box(d, (116 + off, 92), pid, 28, 600, fg)
        targets.append(SpriteTarget("game_player_id", rect_quad(pid_box), {"style": style, "value": pid}))
        # room panel, right side
        px1, py1, px2, py2 = (760, 130, 1250, 560) if style == "bgmi" else (40, 150, 470, 560)
        d.rounded_rectangle((px1, py1, px2, py2), radius=20, fill=panel + (235,))
        d.rectangle((px1, py1, px2, py1 + 64), fill=accent)
        d.text(
            ((px1 + px2) // 2, py1 + 34),
            "CUSTOM ROOM",
            font=load_font("latin", 32, 800),
            fill=bg_top,
            anchor="mm",
        )
        d.text((px1 + 30, py1 + 120), "ROOM ID", font=load_font("latin", 22, 600), fill=accent, anchor="ls")
        rid_box = _text_box(d, (px1 + 30, py1 + 170), rid, 40, 700, fg)
        d.text((px1 + 30, py1 + 240), "PASSWORD", font=load_font("latin", 22, 600), fill=accent, anchor="ls")
        pwd_box = _text_box(d, (px1 + 30, py1 + 290), pwd, 40, 700, fg)
        d.rounded_rectangle((px1 + 30, py2 - 80, px2 - 30, py2 - 24), radius=14, fill=accent)
        d.text(
            ((px1 + px2) // 2, py2 - 52), "START", font=load_font("latin", 28, 800), fill=bg_top, anchor="mm"
        )
        targets.append(SpriteTarget("game_room_id", rect_quad(rid_box), {"style": style, "value": rid}))
        targets.append(SpriteTarget("game_room_password", rect_quad(pwd_box), {"style": style, "value": pwd}))
        context = [SpriteTarget("room_panel", rect_quad((px1, py1, px2, py2)), {})]
        # squad slots in the free area
        sx = 480 if style == "freefire" else 40
        for i in range(4):
            d.rounded_rectangle((sx, 150 + i * 100, sx + 230, 230 + i * 100), radius=14, fill=(0, 0, 0, 90))
            d.text(
                (sx + 20, 200 + i * 100),
                f"SLOT {i + 1}",
                font=load_font("latin", 22, 600),
                fill=fg,
                anchor="ls",
            )
    else:
        context = []
        mode = rng.choice(("loading", "inventory", "result"))
        if mode == "loading":
            d.text(
                (W // 2, 300), "MATCH STARTING", font=load_font("latin", 54, 800), fill=accent, anchor="mm"
            )
            d.rounded_rectangle((340, 360, 940, 384), radius=12, outline=fg, width=3)
            d.rounded_rectangle((344, 364, 344 + rng.randint(80, 560), 380), radius=10, fill=accent)
            d.text(
                (W // 2, 450),
                "Tip: loot early and stay inside the safe zone",
                font=load_font("latin", 26),
                fill=fg,
                anchor="mm",
            )
        elif mode == "inventory":
            for r in range(3):
                for c in range(6):
                    x, y = 120 + c * 180, 150 + r * 170
                    d.rounded_rectangle((x, y, x + 150, y + 140), radius=14, fill=panel + (230,))
                    box = _text_box(d, (x + 20, y + 120), f"x{rng.randint(1, 999)}", 26, 700, accent)
                    distractors.append(SpriteTarget("item_count", rect_quad(box), {}))
        else:
            d.text((W // 2, 200), "MATCH RESULT", font=load_font("latin", 52, 800), fill=accent, anchor="mm")
            for i, lab in enumerate(("RANK", "KILLS", "DAMAGE")):
                x = 240 + i * 280
                d.text((x, 340), lab, font=load_font("latin", 26, 600), fill=fg, anchor="ls")
                box = _text_box(d, (x, 420), f"{rng.randint(1, 4000)}", 56, 800, fg)
                distractors.append(SpriteTarget("score_number", rect_quad(box), {}))
    d.text(
        (W - 24, H - 18),
        f"SPECIMEN UI ({style})",
        font=load_font("latin", 22, 700),
        fill=(255, 100, 100),
        anchor="rs",
    )
    return Sprite(
        np.asarray(img.convert("RGB"), dtype=np.uint8),
        targets,
        distractors=distractors,
        context=context,
        meta={"style": style, "credentials": with_credentials},
    )
