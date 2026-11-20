"""Scene composition for the video suite: sprites, perspective placement, backdrops, ground truth.

A *sprite* is flat artwork (RGB plus an optional alpha mask) with its targets expressed as quads in sprite
pixel coordinates. ``place`` warps the sprite onto a 1280x720 canvas with a 3D-style tilt (roll, yaw, pitch)
and a distance scale, and returns the canvas plus every target quad in canvas pixels. ``finalize``
turns quads into ground-truth records (visible flag, in-frame fraction, shortest side, occlusion).

Visible rule (docs/specs/eval.md 5.2): at least 60 % of the quad inside the frame and its shortest side
at least 24 px.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ...sender.stamp import STAMP_EXCLUSION
from ..common.seeds import np_rng

CANVAS = (1280, 720)  # (w, h)
FOCAL = 1.25 * CANVAS[0]
MIN_SIDE_PX = 24
MIN_IN_FRAME = 0.60

Quad = list[tuple[float, float]]  # 4 corners, clockwise from top-left


@dataclass
class SpriteTarget:
    """A ground-truth target in sprite coordinates (before placement)."""

    category: str
    quad: Quad
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Sprite:
    image: np.ndarray  # RGB uint8 HxWx3
    targets: list[SpriteTarget]
    alpha: np.ndarray | None = None  # HxW uint8, None = opaque rectangle
    distractors: list[SpriteTarget] = field(default_factory=list)  # things that must NOT be masked
    context: list[SpriteTarget] = field(default_factory=list)  # whole-object quads (card, board, phone)
    meta: dict[str, Any] = field(default_factory=dict)


def rect_quad(box: tuple[float, float, float, float]) -> Quad:
    x1, y1, x2, y2 = box
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


# ---- geometry ----------------------------------------------------------------------------------------------
def _rot(roll: float, yaw: float, pitch: float) -> np.ndarray:
    r, y, p = (math.radians(a) for a in (roll, yaw, pitch))
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return rz @ ry @ rx


def projected_quad(
    size: tuple[int, int],
    center: tuple[float, float],
    width_px: float,
    roll: float,
    yaw: float,
    pitch: float,
) -> np.ndarray:
    """Canvas corners (4x2) of a ``size`` (w, h) sprite of nominal width ``width_px`` seen with the given tilt."""
    sw, sh = size
    s = width_px / sw
    pts = np.array(
        [[-sw / 2, -sh / 2, 0], [sw / 2, -sh / 2, 0], [sw / 2, sh / 2, 0], [-sw / 2, sh / 2, 0]],
        dtype=np.float64,
    )
    pts = (pts * s) @ _rot(roll, yaw, pitch).T
    z = FOCAL + pts[:, 2]
    u = center[0] + FOCAL * pts[:, 0] / z
    v = center[1] + FOCAL * pts[:, 1] / z
    return np.stack([u, v], axis=1)


def quad_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def min_side(q: np.ndarray) -> float:
    return float(min(np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)))


def poly_mask(q: np.ndarray, size: tuple[int, int] = CANVAS) -> np.ndarray:
    w, h = size
    m = np.zeros((h, w), np.uint8)
    cv2.fillPoly(m, [np.round(q).astype(np.int32)], 255)
    return m


def in_frame_fraction(q: np.ndarray, size: tuple[int, int] = CANVAS) -> float:
    area = quad_area(q)
    if area <= 1e-6:
        return 0.0
    return min(1.0, float((poly_mask(q, size) > 0).sum()) / area)


def bbox_hits_stamp(q: np.ndarray) -> bool:
    sx1, sy1, sx2, sy2 = STAMP_EXCLUSION
    return not (q[:, 0].max() < sx1 or q[:, 0].min() > sx2 or q[:, 1].max() < sy1 or q[:, 1].min() > sy2)


def placement_ok(q: np.ndarray, margin: int = 6) -> bool:
    return bool(
        q[:, 0].min() >= margin
        and q[:, 1].min() >= margin
        and q[:, 0].max() <= CANVAS[0] - margin
        and q[:, 1].max() <= CANVAS[1] - margin
        and not bbox_hits_stamp(q)
        and quad_area(q) > 100
    )


def convex(q: np.ndarray) -> bool:
    s = []
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        s.append((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]))
    return all(v > 0 for v in s) or all(v < 0 for v in s)


# ---- placement ---------------------------------------------------------------------------------------------
@dataclass
class Placement:
    center: tuple[float, float]
    width_px: float
    roll: float
    yaw: float
    pitch: float
    dst: np.ndarray  # 4x2 canvas quad of the sprite rectangle


def choose_placement(
    rng: random.Random,
    sprite_size: tuple[int, int],
    width_frac: tuple[float, float],
    roll: float,
    yaw: float,
    pitch: float,
    centre_x: tuple[float, float] = (0.3, 0.7),
    centre_y: tuple[float, float] = (0.4, 0.62),
    max_height_frac: float = 0.9,
) -> Placement:
    """Seeded placement inside the frame, avoiding the stamp strip. Raises if nothing fits."""
    sw, sh = sprite_size
    for attempt in range(400):
        frac = rng.uniform(*width_frac)
        shrink = 0.985**attempt  # keep trying a little smaller when the tilt pushes it out of frame
        width = frac * CANVAS[0] * shrink
        if sh / sw * width > CANVAS[1] * max_height_frac:
            width = CANVAS[1] * max_height_frac * sw / sh
        c = (rng.uniform(*centre_x) * CANVAS[0], rng.uniform(*centre_y) * CANVAS[1])
        dst = projected_quad(sprite_size, c, width, roll, yaw, pitch)
        if convex(dst) and placement_ok(dst):
            return Placement(c, width, roll, yaw, pitch, dst)
    raise RuntimeError("could not place sprite inside the frame away from the stamp region")


def warp(canvas: np.ndarray, sprite: Sprite, pl: Placement) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Composite `sprite` on `canvas`. Returns (image, sprite->canvas homography, coverage mask uint8)."""
    sh, sw = sprite.image.shape[:2]
    src = np.array([[0, 0], [sw, 0], [sw, sh], [0, sh]], dtype=np.float32)
    # Pre-shrink large sprites to about the final size so the perspective warp does not alias.
    k = min(1.0, 1.5 * pl.width_px / sw)
    img = sprite.image
    alpha = sprite.alpha if sprite.alpha is not None else np.full((sh, sw), 255, np.uint8)
    if k < 0.98:
        nw, nh = max(2, round(sw * k)), max(2, round(sh * k))
        img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        alpha = cv2.resize(alpha, (nw, nh), interpolation=cv2.INTER_AREA)
    ssz = np.array([[0, 0], [img.shape[1], 0], [img.shape[1], img.shape[0]], [0, img.shape[0]]], np.float32)
    m_small = cv2.getPerspectiveTransform(ssz, pl.dst.astype(np.float32))
    w, h = CANVAS
    warped = cv2.warpPerspective(img, m_small, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    amask = cv2.warpPerspective(
        alpha, m_small, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
    )
    if sprite.alpha is None:  # anti-aliased rectangle edge
        edge = np.zeros((h, w), np.uint8)
        cv2.fillPoly(edge, [np.round(pl.dst).astype(np.int32)], 255, lineType=cv2.LINE_AA)
        amask = np.minimum(amask, edge)
    a = (amask.astype(np.float32) / 255.0)[..., None]
    out = (canvas.astype(np.float32) * (1 - a) + warped.astype(np.float32) * a).astype(np.uint8)
    h_full = cv2.getPerspectiveTransform(src, pl.dst.astype(np.float32))
    return out, h_full, amask


def transform_quad(h: np.ndarray, quad: Quad) -> np.ndarray:
    pts = np.array(quad, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, h).reshape(-1, 2)


@dataclass
class Placed:
    image: np.ndarray
    targets: list[dict[str, Any]]
    distractors: list[dict[str, Any]]
    context: list[dict[str, Any]]
    sprite_mask: np.ndarray
    homography: np.ndarray
    placement: Placement


def _record(t: SpriteTarget, h: np.ndarray) -> dict[str, Any]:
    q = transform_quad(h, t.quad)
    rec: dict[str, Any] = {
        "category": t.category,
        "quad": [[round(float(x), 2), round(float(y), 2)] for x, y in q],
    }
    rec.update(t.attrs)
    rec["_q"] = q
    return rec


def place(canvas: np.ndarray, sprite: Sprite, pl: Placement) -> Placed:
    img, h, amask = warp(canvas, sprite, pl)
    return Placed(
        img,
        [_record(t, h) for t in sprite.targets],
        [_record(t, h) for t in sprite.distractors],
        [_record(t, h) for t in sprite.context],
        amask,
        h,
        pl,
    )


def finalize(recs: list[dict[str, Any]], occluder: np.ndarray | None = None) -> list[dict[str, Any]]:
    """Add visible / in_frame_fraction / min_side_px / occlusion_fraction and drop the private ``_q`` field."""
    out = []
    for r in recs:
        q = r.pop("_q")
        frac = in_frame_fraction(q)
        ms = min_side(q)
        r["in_frame_fraction"] = round(frac, 3)
        r["min_side_px"] = round(ms, 1)
        occ = 0.0
        if occluder is not None:
            qm = poly_mask(q) > 0
            tot = int(qm.sum())
            occ = float((qm & (occluder > 0)).sum()) / tot if tot else 0.0
        r["occlusion_fraction"] = round(occ, 3)
        r["visible"] = bool(frac >= MIN_IN_FRAME and ms >= MIN_SIDE_PX)
        out.append(r)
    return out


# ---- backdrops ---------------------------------------------------------------------------------------------
BACKDROP_STYLES = ("desk", "wood", "wall", "dark_room", "cloth", "street")


def backdrop(g: np.random.Generator, style: str, size: tuple[int, int] = CANVAS) -> np.ndarray:
    """Seeded textured background with a little clutter (plain shapes only, never text or digits)."""
    w, h = size
    ramp = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    if style == "desk":
        top = np.array([96, 84, 70], np.float32) + g.integers(-12, 13, 3)
        bot = np.array([140, 122, 100], np.float32) + g.integers(-12, 13, 3)
        img = top * (1 - ramp) + bot * ramp
        img = np.broadcast_to(img, (h, w, 3)).copy()
        img += g.normal(0, 3.0, (h, w, 1)).astype(np.float32)
    elif style == "wood":
        base = np.array([120, 84, 52], np.float32) + g.integers(-15, 16, 3)
        stripes = np.sin(np.linspace(0, g.uniform(40, 90), h) + g.uniform(0, 6))[:, None, None] * 10
        img = np.broadcast_to(base + stripes, (h, w, 3)).copy()
        img += g.normal(0, 5.0, (h, w, 1)).astype(np.float32)
    elif style == "wall":
        base = np.array([205, 200, 190], np.float32) + g.integers(-20, 21, 3)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        vig = 1 - 0.25 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2) * 4
        img = base * vig[..., None] + g.normal(0, 2.5, (h, w, 1)).astype(np.float32)
    elif style == "dark_room":
        base = np.array([28, 30, 40], np.float32) + g.integers(-6, 7, 3)
        img = np.broadcast_to(base * (0.7 + 0.6 * ramp), (h, w, 3)).copy()
        img += g.normal(0, 2.0, (h, w, 1)).astype(np.float32)
        for _ in range(int(g.integers(4, 9))):  # bokeh lights
            cx, cy, r = int(g.integers(0, w)), int(g.integers(0, h)), int(g.integers(12, 40))
            col = g.integers(80, 220, 3).astype(np.float32)
            layer = np.zeros((h, w, 3), np.float32)
            cv2.circle(layer, (cx, cy), r, tuple(float(c) for c in col), -1, cv2.LINE_AA)
            layer = cv2.GaussianBlur(layer, (0, 0), 6)
            img += layer * 0.35
    elif style == "cloth":
        base = np.array([70, 90, 110], np.float32) + g.integers(-15, 16, 3)
        tex = g.normal(0, 12, (h // 4 + 1, w // 4 + 1)).astype(np.float32)
        tex = cv2.resize(tex, (w, h), interpolation=cv2.INTER_NEAREST)[..., None]
        img = np.broadcast_to(base, (h, w, 3)).copy() + tex
    else:  # street: sky, building blocks, road
        sky = np.array([170, 195, 220], np.float32) + g.integers(-15, 16, 3)
        img = np.broadcast_to(sky, (h, w, 3)).copy()
        x = 0
        while x < w:
            bw = int(g.integers(80, 260))
            bh = int(g.integers(120, 380))
            img[h - bh - 120 : h - 120, x : x + bw] = g.integers(90, 190, 3)
            x += bw + int(g.integers(0, 20))
        img[h - 120 :, :] = np.array([85, 85, 90], np.float32) + g.integers(-8, 9, 3)
        img += g.normal(0, 3.0, (h, w, 1)).astype(np.float32)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ov = img.copy()  # clutter: a few plain boxes / ellipses (no text, no digits)
    for _ in range(int(g.integers(2, 6))):
        x1, y1 = int(g.integers(0, w - 80)), int(g.integers(0, h - 60))
        x2, y2 = x1 + int(g.integers(40, 220)), y1 + int(g.integers(30, 160))
        col = tuple(int(c) for c in g.integers(30, 230, 3))
        if g.random() < 0.5:
            cv2.rectangle(ov, (x1, y1), (x2, y2), col, -1, cv2.LINE_AA)
        else:
            cv2.ellipse(
                ov,
                ((x1 + x2) // 2, (y1 + y2) // 2),
                ((x2 - x1) // 2, (y2 - y1) // 2),
                0,
                0,
                360,
                col,
                -1,
                cv2.LINE_AA,
            )
    return cv2.addWeighted(ov, 0.35, img, 0.65, 0)


def make_backdrop(seed: int, name: str, style: str | None = None) -> tuple[np.ndarray, str]:
    g = np_rng(seed, "backdrop", name)
    st = style or BACKDROP_STYLES[int(g.integers(0, len(BACKDROP_STYLES)))]
    return backdrop(g, st), st
