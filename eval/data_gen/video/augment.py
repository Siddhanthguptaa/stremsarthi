"""Augmentation for the video suite: tilt, distance, glare, motion/defocus blur, JPEG, dimming, occlusion.

Geometry (roll/yaw/pitch up to 25 degrees, distance scale) is applied by ``compose.place``; this module samples
the parameters and applies the image-level effects afterwards. Every parameter that was applied is returned
as a plain dict and stored in the ground truth (``truth["aug"]``), so a miss can be explained by its cause.

Levels: ``clean`` (20 %), ``light`` (40 %), ``heavy`` (40 %), picked by item index so the mix is fixed.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any

import cv2
import numpy as np

MAX_TILT_DEG = 25.0
LEVELS = ("clean", "light", "light", "heavy", "heavy")  # index % 5 -> level (20/40/40)


def level_for(index: int) -> str:
    return LEVELS[index % len(LEVELS)]


@dataclass
class Glare:
    cx: float  # fraction of the object bbox
    cy: float
    rx: float  # fraction of bbox width
    ry: float
    angle: float
    strength: float
    streak: bool


@dataclass
class AugParams:
    level: str
    roll: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    width_frac: tuple[float, float] = (0.3, 0.5)  # sampling range for the distance scale
    glare: list[Glare] = field(default_factory=list)
    blur_kind: str = "none"  # none | motion | defocus | both
    motion_len: int = 0
    motion_angle: float = 0.0
    defocus_sigma: float = 0.0
    jpeg_quality: int = 0  # 0 = no JPEG pass
    gain: float = 1.0
    gamma: float = 1.0
    noise_sigma: float = 0.0
    occlusion_target: float = 0.0  # requested fraction; the measured one is in each target record
    occluder_side: str = "none"
    occluder_color: tuple[int, int, int] = (0, 0, 0)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["width_frac"] = [round(v, 3) for v in self.width_frac]
        for g in d["glare"]:
            for k in ("cx", "cy", "rx", "ry", "angle", "strength"):
                g[k] = round(g[k], 3)
        for k in (
            "roll",
            "yaw",
            "pitch",
            "motion_angle",
            "defocus_sigma",
            "gain",
            "gamma",
            "noise_sigma",
            "occlusion_target",
        ):
            d[k] = round(d[k], 3)
        return d


def sample_params(
    rng: random.Random,
    level: str,
    width_frac: tuple[float, float] = (0.2, 0.55),
    allow_tilt: bool = True,
    allow_occlusion: bool = True,
) -> AugParams:
    """Draw augmentation parameters for `level`. `width_frac` is the object's full distance range."""
    lo, hi = width_frac
    mid = (lo + hi) / 2
    u = rng.uniform
    p = AugParams(level=level)
    if level == "clean":
        p.width_frac = (mid, hi)
        tilt = 4.0
    elif level == "light":
        p.width_frac = (lo + 0.25 * (hi - lo), hi)
        tilt = 12.0
    else:
        p.width_frac = (lo, mid)
        tilt = MAX_TILT_DEG
    if allow_tilt:
        p.roll, p.yaw, p.pitch = u(-tilt, tilt), u(-tilt, tilt), u(-tilt * 0.8, tilt * 0.8)
    if level == "clean":
        p.jpeg_quality = rng.choice((0, 92, 95))
        p.gain = u(0.92, 1.05)
        return p
    if level == "light":
        if rng.random() < 0.5:
            p.glare = [_glare(rng, 0.35, 0.65)]
        if rng.random() < 0.5:
            p.blur_kind, p.defocus_sigma = "defocus", u(0.5, 1.2)
        elif rng.random() < 0.4:
            p.blur_kind, p.motion_len, p.motion_angle = "motion", rng.randint(3, 7), u(0, 180)
        p.jpeg_quality = rng.randint(70, 90)
        p.gain, p.gamma, p.noise_sigma = u(0.72, 1.0), u(0.9, 1.15), u(0, 2.5)
        if allow_occlusion and rng.random() < 0.4:
            p.occlusion_target = u(0.04, 0.15)
    else:  # heavy
        p.glare = [_glare(rng, 0.5, 0.95) for _ in range(rng.randint(1, 3))]
        r = rng.random()
        if r < 0.4:
            p.blur_kind, p.defocus_sigma = "defocus", u(1.5, 3.0)
        elif r < 0.8:
            p.blur_kind, p.motion_len, p.motion_angle = "motion", rng.randint(7, 17), u(0, 180)
        else:
            p.blur_kind, p.defocus_sigma = "both", u(0.8, 1.6)
            p.motion_len, p.motion_angle = rng.randint(5, 11), u(0, 180)
        p.jpeg_quality = rng.randint(25, 60)
        p.gain, p.gamma, p.noise_sigma = u(0.35, 0.8), u(0.85, 1.4), u(2.0, 7.0)
        if allow_occlusion and rng.random() < 0.75:
            p.occlusion_target = u(0.10, 0.40)
    if p.occlusion_target > 0:
        p.occluder_side = rng.choice(("left", "right", "top", "bottom"))
        p.occluder_color = rng.choice(
            ((205, 160, 130), (150, 105, 80), (30, 30, 34), (235, 235, 230), (70, 90, 150))
        )
    return p


def _glare(rng: random.Random, lo: float, hi: float) -> Glare:
    return Glare(
        cx=rng.uniform(0.15, 0.85),
        cy=rng.uniform(0.15, 0.85),
        rx=rng.uniform(0.12, 0.45),
        ry=rng.uniform(0.08, 0.3),
        angle=rng.uniform(0, 180),
        strength=rng.uniform(lo, hi),
        streak=rng.random() < 0.3,
    )


# ---- effects -----------------------------------------------------------------------------------------------
def _bbox_of(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask > 8)
    if len(xs) == 0:
        return 0, 0, mask.shape[1], mask.shape[0]
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def add_glare(img: np.ndarray, g: Glare, bbox: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    bw, bh = x2 - x1, y2 - y1
    cx, cy = x1 + g.cx * bw, y1 + g.cy * bh
    rx, ry = max(4.0, g.rx * bw), max(3.0, g.ry * bw * 0.6)
    if g.streak:
        rx, ry = rx * 1.8, max(2.0, ry * 0.25)
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = math.radians(g.angle)
    dx, dy = xx - cx, yy - cy
    u = (dx * math.cos(a) + dy * math.sin(a)) / rx
    v = (-dx * math.sin(a) + dy * math.cos(a)) / ry
    blob = np.exp(-(u * u + v * v) * 1.5) * g.strength
    out = img.astype(np.float32)
    out += (255.0 - out) * blob[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def motion_kernel(length: int, angle: float) -> np.ndarray:
    k = np.zeros((length, length), np.float32)
    c = (length - 1) / 2
    a = math.radians(angle)
    cv2.line(
        k,
        (round(c - c * math.cos(a)), round(c - c * math.sin(a))),
        (round(c + c * math.cos(a)), round(c + c * math.sin(a))),
        1.0,
        1,
    )
    s = k.sum()
    return k / s if s > 0 else np.ones((1, 1), np.float32)


def add_occluder(
    img: np.ndarray, sprite_mask: np.ndarray, p: AugParams, g: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Draw a hand/object-like occluder covering about `p.occlusion_target` of the object's bounding box."""
    h, w = img.shape[:2]
    occ = np.zeros((h, w), np.uint8)
    if p.occlusion_target <= 0:
        return img, occ
    x1, y1, x2, y2 = _bbox_of(sprite_mask)
    bw, bh = x2 - x1, y2 - y1
    f = p.occlusion_target
    wob = lambda n: int(g.integers(-n, n + 1)) if n > 0 else 0  # noqa: E731
    if p.occluder_side in ("left", "right"):
        cut = int(f * bw)
        xa, xb = (x1 - 40, x1 + cut) if p.occluder_side == "left" else (x2 - cut, x2 + 40)
        edge = xb if p.occluder_side == "left" else xa
        pts = [
            (xa, y1 - 40),
            (xb + wob(8), y1 - 40),
            (edge + wob(max(1, bw // 20)), (y1 + y2) // 2),
            (xb, y2 + 40),
            (xa, y2 + 40),
        ]
        if p.occluder_side == "right":
            pts = [
                (xb, y1 - 40),
                (xa + wob(8), y1 - 40),
                (edge + wob(max(1, bw // 20)), (y1 + y2) // 2),
                (xa, y2 + 40),
                (xb, y2 + 40),
            ]
    else:
        cut = int(f * bh)
        ya, yb = (y1 - 40, y1 + cut) if p.occluder_side == "top" else (y2 - cut, y2 + 40)
        edge = yb if p.occluder_side == "top" else ya
        if p.occluder_side == "top":
            pts = [
                (x1 - 40, ya),
                (x2 + 40, ya),
                (x2 + 40, yb),
                ((x1 + x2) // 2 + wob(max(1, bw // 20)), edge + wob(6)),
                (x1 - 40, yb),
            ]
        else:
            pts = [
                (x1 - 40, yb),
                (x2 + 40, yb),
                (x2 + 40, ya),
                ((x1 + x2) // 2 + wob(max(1, bw // 20)), edge + wob(6)),
                (x1 - 40, ya),
            ]
    poly = np.array(pts, np.int32)
    cv2.fillPoly(occ, [poly], 255, lineType=cv2.LINE_AA)
    out = img.copy()
    col = np.array(p.occluder_color, np.float32)
    shade = 1.0 + (g.normal(0, 0.03, (h, w, 1))).astype(np.float32)
    layer = np.clip(col * shade, 0, 255)
    a = (occ.astype(np.float32) / 255.0)[..., None]
    out = (out.astype(np.float32) * (1 - a) + layer * a).astype(np.uint8)
    return out, occ


def apply_effects(
    img: np.ndarray, sprite_mask: np.ndarray, p: AugParams, g: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Occlusion, glare, blur, dimming, noise, JPEG in that order. Returns (image, occluder mask)."""
    img, occ = add_occluder(img, sprite_mask, p, g)
    bbox = _bbox_of(sprite_mask)
    for gl in p.glare:
        img = add_glare(img, gl, bbox)
    if p.blur_kind in ("motion", "both") and p.motion_len >= 2:
        img = cv2.filter2D(img, -1, motion_kernel(p.motion_len, p.motion_angle))
    if p.blur_kind in ("defocus", "both") and p.defocus_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), p.defocus_sigma)
    if p.gain != 1.0 or p.gamma != 1.0:
        f = (img.astype(np.float32) / 255.0) ** p.gamma * p.gain
        img = np.clip(f * 255.0, 0, 255).astype(np.uint8)
    if p.noise_sigma > 0:
        n = g.normal(0, p.noise_sigma, img.shape).astype(np.float32)
        img = np.clip(img.astype(np.float32) + n, 0, 255).astype(np.uint8)
    if p.jpeg_quality > 0:
        img = jpeg_roundtrip(img, p.jpeg_quality)
    return img, occ


def jpeg_roundtrip(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)[..., ::-1].copy()
