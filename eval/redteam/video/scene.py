"""Scene building blocks for the red team: sprites, posed placement, effects, per-frame truth and read-back values.

Everything is synthetic SPECIMEN data from ``eval.data_gen.video`` (marked SPECIMEN, Verhoeff-valid fake numbers).
"""

from __future__ import annotations

import random
from typing import Any

import cv2
import numpy as np

from ...data_gen.common import identifiers, specimen
from ...data_gen.common.seeds import np_rng, py_rng
from ...data_gen.video import idcards, plates, qr, textpii
from ...data_gen.video.augment import AugParams, Glare, apply_effects
from ...data_gen.video.compose import (
    CANVAS,
    Placement,
    Sprite,
    SpriteTarget,
    finalize,
    make_backdrop,
    place,
    projected_quad,
    rect_quad,
)
from .model import (
    EXPOSED_MIN_IN_FRAME,
    EXPOSED_MIN_SIDE_PX,
    NO_READBACK,
    SPEC_MIN_IN_FRAME,
    SPEC_MIN_SIDE_PX,
    Expect,
    Frame,
)

W, H = CANVAS
CENTER = (W / 2.0, H / 2.0)


# ---------------------------------------------------------------------------------------------- sprites
def sprite_card(seed: int, kind: str = "aadhaar", tag: str = "") -> Sprite:
    rng = py_rng(seed, "rt-card", kind, tag)
    return idcards.aadhaar_sprite(rng) if kind == "aadhaar" else idcards.pan_sprite(rng)


def sprite_qr(seed: int, tag: str = "") -> Sprite:
    """A positive UPI QR poster (SPECIMEN payload ``upi://pay?pa=zz-specimen-...``)."""
    rng = py_rng(seed, "rt-qr", tag)
    q = qr.render_qr_poster(rng, handle=rng.choice(specimen.UPI_HANDLES))
    h, w = q.image.shape[:2]
    t = SpriteTarget("upi_qr", rect_quad(q.qr_bbox), {"payload": q.payload, "upi_id": q.upi_id})
    return Sprite(q.image, [t], context=[SpriteTarget("poster", rect_quad((0, 0, w, h)), {})])


def sprite_plate(seed: int, tag: str = "") -> Sprite:
    rng = py_rng(seed, "rt-plate", tag)
    return plates.render_vehicle(rng, identifiers.plate_standard(rng), "car")


def sprite_text(seed: int, kind: str, tag: str = "") -> Sprite:
    rng = py_rng(seed, "rt-text", kind, tag)
    return textpii.render_text_screen(rng, kind, layout="doc")


def backdrop(seed: int, tag: str = "", style: str | None = None) -> np.ndarray:
    """RGB 1280x720 backdrop (plain shapes, no text)."""
    return make_backdrop(seed, f"rt-{tag}", style)[0]


# ---------------------------------------------------------------------------------------------- poses
def placement(
    sprite: Sprite,
    center: tuple[float, float] = CENTER,
    width: float = 0.4 * W,
    roll: float = 0.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
) -> Placement:
    sh, sw = sprite.image.shape[:2]
    dst = projected_quad((sw, sh), center, width, roll, yaw, pitch)
    return Placement(center, width, roll, yaw, pitch, dst)


def aug(
    *,
    jpeg: int = 90,
    noise: float = 1.5,
    glare: list[Glare] | None = None,
    blur: str = "none",
    motion_len: int = 0,
    motion_angle: float = 0.0,
    defocus: float = 0.0,
    gain: float = 1.0,
    gamma: float = 1.0,
    occlusion: float = 0.0,
    side: str = "right",
    colour: tuple[int, int, int] = (205, 160, 130),
) -> AugParams:
    p = AugParams(level="heavy")
    p.jpeg_quality, p.noise_sigma, p.gain, p.gamma = jpeg, noise, gain, gamma
    p.glare = list(glare or [])
    p.blur_kind, p.motion_len, p.motion_angle, p.defocus_sigma = blur, motion_len, motion_angle, defocus
    p.occlusion_target, p.occluder_side, p.occluder_color = occlusion, side, colour
    return p


def exposed(t: dict[str, Any]) -> bool:
    return bool(t["in_frame_fraction"] >= EXPOSED_MIN_IN_FRAME and t["min_side_px"] >= EXPOSED_MIN_SIDE_PX)


def spec_visible(t: dict[str, Any]) -> bool:
    return bool(t["in_frame_fraction"] >= SPEC_MIN_IN_FRAME and t["min_side_px"] >= SPEC_MIN_SIDE_PX)


# ---------------------------------------------------------------------------------------------- expectations
def expect_for(t: dict[str, Any]) -> Expect:
    cat = t["category"]
    if cat in ("aadhaar", "pan") and t.get("number"):
        return Expect("text", (str(t["number"]),))
    if cat == "upi_qr" and t.get("payload"):
        return Expect("qr", (str(t["payload"]),))
    if cat.startswith("text_") and t.get("value"):
        return Expect("text", (str(t["value"]),))
    if cat == "plate" and t.get("text"):
        return Expect("text", (str(t["text"]),))
    return NO_READBACK


def truth_target(t: dict[str, Any], j: int) -> dict[str, Any]:
    out = {k: v for k, v in t.items() if k not in ("fields",)}
    out["j"] = j
    out["aux"] = bool(t.get("aux", False))
    out["visible_spec"] = spec_visible(t)
    out["visible"] = exposed(t)  # the red-team exposure rule (stricter than the dataset's)
    return out


def make_frame(
    img_rgb: np.ndarray, targets: list[dict[str, Any]], *, tag: str = "", dropped: bool = False
) -> Frame:
    tt = [truth_target(t, j) for j, t in enumerate(targets)]
    exp = {t["j"]: expect_for(t) for t in tt if not t["aux"]}
    bgr = np.ascontiguousarray(img_rgb[..., ::-1])
    return Frame(bgr, {"targets": tt, "distractors": [], "context": []}, exp, dropped, tag)


# ---------------------------------------------------------------------------------------------- rendering
def render(
    bg_rgb: np.ndarray,
    sprite: Sprite,
    pl: Placement,
    p: AugParams | None,
    g: np.random.Generator,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Place ``sprite`` on a copy of ``bg_rgb`` and apply the effects. Returns (RGB image, finalised targets)."""
    placed = place(bg_rgb.copy(), sprite, pl)
    img, occ = apply_effects(placed.image, placed.sprite_mask, p or aug(), g)
    return img, finalize(placed.targets, occ)


def render_frame(
    bg_rgb: np.ndarray,
    sprite: Sprite,
    pl: Placement,
    p: AugParams | None,
    g: np.random.Generator,
    *,
    tag: str = "",
) -> Frame:
    img, targets = render(bg_rgb, sprite, pl, p, g)
    return make_frame(img, targets, tag=tag)


def empty_frame(bg_rgb: np.ndarray, g: np.random.Generator, *, tag: str = "", dropped: bool = False) -> Frame:
    img = bg_rgb
    n = g.normal(0, 1.5, img.shape).astype(np.float32)
    img = np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
    return make_frame(img, [], tag=tag, dropped=dropped)


def jitter_pose(g: np.random.Generator, px: float = 2.0, deg: float = 0.4) -> tuple[float, float, float]:
    return float(g.normal(0, px)), float(g.normal(0, px)), float(g.normal(0, deg))


def glare_on(cx: float, cy: float, strength: float, rx: float = 0.35, ry: float = 0.25) -> Glare:
    return Glare(cx=cx, cy=cy, rx=rx, ry=ry, angle=20.0, strength=strength, streak=False)


# ---------------------------------------------------------------------------------------------- nesting
def nested_sprite(
    seed: int,
    inner: Sprite,
    *,
    kind: str,
    inner_width: float,
    inner_yaw: float,
    inner_roll: float,
    tag: str,
) -> Sprite:
    """A screen or a printed photo that itself shows ``inner`` (screen of a screen, photo of a photo).

    The inner artwork is placed on its own backdrop, then framed (phone/monitor bezel or white photo border),
    degraded (moire for a screen, colour cast and glossy streak for a print) and returned as a new sprite whose
    targets are the inner targets mapped into the frame. Used as the artwork of the outer scene.
    """
    g = np_rng(seed, "rt-nest", tag)
    inner_bg = backdrop(seed, f"nest-{tag}", "dark_room" if kind == "screen" else "desk")
    pl = placement(inner, CENTER, inner_width, inner_roll, inner_yaw, 0.0)
    img, targets = render(inner_bg, inner, pl, aug(jpeg=85, noise=1.0, defocus=0.6), g)
    pad = 36 if kind == "screen" else 44
    colour = (18, 18, 20) if kind == "screen" else (246, 244, 238)
    framed = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=colour)
    fh, fw = framed.shape[:2]
    yy, xx = np.mgrid[0:fh, 0:fw].astype(np.float32)
    f = framed.astype(np.float32)
    if kind == "screen":  # moire of the display's pixel grid against the camera sensor
        period = 3.1 + float(g.uniform(0, 0.8))
        grating = 0.5 + 0.5 * np.sin(2 * np.pi * (xx * 0.93 + yy * 0.37) / period)
        f *= (0.82 + 0.18 * grating)[..., None]
    else:  # a print: warm cast, paper texture and a glossy diagonal streak
        f *= np.array([0.96, 1.0, 1.06], np.float32)  # RGB order: slightly warm
        f += g.normal(0, 3.0, f.shape).astype(np.float32)
        streak = np.exp(-((((xx - fw * 0.6) + (yy - fh * 0.5) * 0.7) / (fw * 0.06)) ** 2)) * 0.5
        f += (255.0 - f) * streak[..., None]
    framed = np.clip(f, 0, 255).astype(np.uint8)
    sprite_targets: list[SpriteTarget] = []
    for t in targets:
        attrs = {
            k: v
            for k, v in t.items()
            if k
            not in ("category", "quad", "in_frame_fraction", "min_side_px", "occlusion_fraction", "visible")
        }
        q = [(float(x) + pad, float(y) + pad) for x, y in t["quad"]]
        sprite_targets.append(SpriteTarget(t["category"], q, attrs))
    return Sprite(framed, sprite_targets)


def rng_for(seed: int, *names: object) -> random.Random:
    return py_rng(seed, *names)
