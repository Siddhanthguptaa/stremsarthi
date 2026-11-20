"""SPECIMEN QR scene generator for the ``upi_qr`` tests and acceptance run (never imported by the detector).

Everything is synthetic. Payment payloads are ``upi://pay?pa=zz-specimen-<hex>@specimen&pn=SPECIMEN...``
(the handle ``specimen`` does not exist), "other" payloads are ``https://example.org/specimen/<hex>``. No
real UPI ID, account or person appears anywhere.

``make_qr_scene`` draws a QR on a cluttered 1280x720 frame with distance (module size), in-plane rotation plus
perspective tilt (up to 25 degrees), blur, glare, noise, JPEG artefacts and partial occlusion, and returns the
ground-truth quad of the code including its quiet zone. ``augmented_set`` builds the acceptance set.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

CANVAS_W, CANVAS_H = 1280, 720
QUIET = 4  # modules of quiet zone around the symbol


@dataclass
class QrScene:
    image: np.ndarray  # BGR uint8 720x1280x3
    kind: str  # "upi" | "other" | "none"
    quad: np.ndarray | None = None  # (4, 2) corners of the symbol INCLUDING quiet zone, clockwise from TL
    symbol_quad: np.ndarray | None = None  # (4, 2) corners of the dark modules only
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def positive(self) -> bool:
        return self.kind == "upi"

    @property
    def has_qr(self) -> bool:
        return self.quad is not None


def payment_payload(rng: random.Random) -> str:
    tag = f"{rng.getrandbits(32):08x}"
    return f"upi://pay?pa=zz-specimen-{tag}@specimen&pn=SPECIMEN%20SHOP&am={rng.randint(10, 999)}&cu=INR"


def other_payload(rng: random.Random) -> str:
    tag = f"{rng.getrandbits(32):08x}"
    return rng.choice(
        [
            f"https://example.org/specimen/{tag}",
            f"https://example.org/menu?table={rng.randint(1, 40)}&s={tag}",
            f"WIFI:T:WPA;S:SPECIMEN-{tag};P:specimen-not-a-password;;",
            f"SPECIMEN contact card {tag}",
        ]
    )


def qr_matrix(payload: str, *, ec: str = "H") -> np.ndarray:
    """Boolean module matrix (True = dark) without quiet zone, made by the ``qrcode`` package."""
    import qrcode  # noqa: PLC0415 - test-only dependency

    level = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }[ec]
    qr = qrcode.QRCode(error_correction=level, border=0, box_size=1)
    qr.add_data(payload)
    qr.make(fit=True)
    return np.array(qr.get_matrix(), dtype=bool)


def _patch(mods: np.ndarray, side_px: int, *, dark: int = 20, light: int = 245) -> np.ndarray:
    """Gray patch (side_px square) of the symbol with its quiet zone."""
    n = mods.shape[0] + 2 * QUIET
    grid = np.zeros((n, n), dtype=bool)
    grid[QUIET:-QUIET, QUIET:-QUIET] = mods
    big = np.kron(grid, np.ones((8, 8), dtype=np.uint8)).astype(np.uint8)
    img = np.where(big > 0, dark, light).astype(np.uint8)
    interp = cv2.INTER_AREA if side_px < img.shape[0] else cv2.INTER_LINEAR
    return cv2.resize(img, (side_px, side_px), interpolation=interp)


def _background(rng: random.Random, np_rng: np.random.Generator) -> np.ndarray:
    """Cluttered desk/screen background: gradient, rectangles, text lines, noise (edges but no QR)."""
    h, w = CANVAS_H, CANVAS_W
    c0 = np.array([rng.randint(40, 200) for _ in range(3)], dtype=np.float32)
    c1 = np.array([rng.randint(40, 200) for _ in range(3)], dtype=np.float32)
    t = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :, None]
    img = (c0[None, None, :] * (1 - t) + c1[None, None, :] * t).repeat(h, axis=0)
    img = np.ascontiguousarray(img.astype(np.uint8))
    for _ in range(rng.randint(4, 10)):
        x1, y1 = rng.randint(0, w - 80), rng.randint(0, h - 60)
        x2, y2 = min(w - 1, x1 + rng.randint(40, 420)), min(h - 1, y1 + rng.randint(30, 260))
        col = tuple(rng.randint(20, 235) for _ in range(3))
        cv2.rectangle(img, (x1, y1), (x2, y2), col, -1 if rng.random() < 0.6 else rng.randint(1, 5))
    for _ in range(rng.randint(3, 9)):
        txt = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ") for _ in range(rng.randint(6, 22)))
        col = tuple(rng.randint(0, 255) for _ in range(3))
        cv2.putText(
            img,
            txt,
            (rng.randint(0, w - 300), rng.randint(40, h - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            rng.uniform(0.5, 1.6),
            col,
            rng.randint(1, 3),
            cv2.LINE_AA,
        )
    noise = np_rng.normal(0.0, rng.uniform(1.0, 5.0), img.shape)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _homography(
    side: float,
    centre: tuple[float, float],
    *,
    rot_deg: float,
    yaw_deg: float,
    pitch_deg: float,
    focal: float = 1000.0,
) -> np.ndarray:
    """Corners of a square of ``side`` px (patch frame TL,TR,BR,BL) after rotation about z, then yaw/pitch."""
    h = side / 2.0
    pts = np.array([[-h, -h, 0], [h, -h, 0], [h, h, 0], [-h, h, 0]], dtype=np.float64)
    rz, ry, rx = (math.radians(a) for a in (rot_deg, yaw_deg, pitch_deg))
    mz = np.array([[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]])
    my = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    mx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    rotated = pts @ (mx @ my @ mz).T
    z = rotated[:, 2] + focal
    xy = rotated[:, :2] * (focal / z)[:, None] + np.array(centre)
    return xy.astype(np.float32)


def _glare(img: np.ndarray, rng: random.Random, quad: np.ndarray, strength: float) -> None:
    """A bright, soft elliptical highlight over part of the code (specular reflection on glossy paper/screen)."""
    span = float(np.ptp(quad[:, 0]))
    cx, cy = quad.mean(axis=0) + np.array([rng.uniform(-0.25, 0.25), rng.uniform(-0.25, 0.25)]) * span
    ax = max(10.0, span * rng.uniform(0.25, 0.55))
    ay = max(10.0, span * rng.uniform(0.15, 0.4))
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]].astype(np.float32)
    ang = math.radians(rng.uniform(0, 180))
    dx, dy = xx - cx, yy - cy
    u = dx * math.cos(ang) + dy * math.sin(ang)
    v = -dx * math.sin(ang) + dy * math.cos(ang)
    g = np.exp(-((u / ax) ** 2 + (v / ay) ** 2)) * strength
    out = img.astype(np.float32) + g[..., None] * 255.0
    img[:] = np.clip(out, 0, 255).astype(np.uint8)


def make_qr_scene(
    kind: str,
    seed: int,
    *,
    side_px: int | None = None,
    module_px: float | None = None,
    rot_deg: float | None = None,
    tilt_deg: float | None = None,
    blur_sigma: float = 0.0,
    glare: float = 0.0,
    occlusion: float = 0.0,
    jpeg_quality: int | None = None,
    noise: float = 0.0,
    ec: str = "M",
    payload: str | None = None,
    centre: tuple[float, float] | None = None,
    protect_finders: bool = True,
) -> QrScene:
    """One frame with (``kind`` in ``upi``/``other``) or without (``none``) a QR code.

    Distance is ``module_px`` (pixels per module before perspective; a scanner needs about 2 px or more) or
    ``side_px`` (side of the symbol with its quiet zone). ``rot_deg`` is the in-plane rotation, ``tilt_deg`` the combined yaw/pitch
    (spec: up to 25 degrees). ``occlusion`` is the fraction of the symbol area covered by one opaque patch;
    with ``protect_finders`` the patch avoids the three finder patterns (a covered finder makes the code
    unreadable for any scanner, which the hard-set tests cover separately).
    """
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    img = _background(rng, np_rng)
    params: dict[str, Any] = {}
    if kind == "none":
        # hard negatives: a barcode-like stripe block or a nested-squares logo (finder lookalike)
        if rng.random() < 0.5:
            x, y = rng.randint(40, 900), rng.randint(40, 500)
            for k in range(rng.randint(20, 40)):
                wbar = rng.randint(1, 4)
                cv2.rectangle(img, (x + k * 6, y), (x + k * 6 + wbar, y + 90), (10, 10, 10), -1)
        else:
            x, y, s = rng.randint(60, 900), rng.randint(60, 500), rng.randint(50, 110)
            for k, col in enumerate((10, 240, 10, 240, 10)):
                m = int(k * s / 10)
                cv2.rectangle(img, (x + m, y + m), (x + s - m, y + s - m), (col,) * 3, -1)
        return QrScene(img, "none", None, None, {"hard_negative": True})

    text = payload if payload is not None else (payment_payload(rng) if kind == "upi" else other_payload(rng))
    mods = qr_matrix(text, ec=ec)
    n = mods.shape[0]
    if module_px is not None:  # distance as pixels per module (readable codes need >= 2 px)
        side = int(round((n + 2 * QUIET) * module_px))
    else:
        side = int(side_px if side_px is not None else rng.randint(150, 300))
    rot = rng.uniform(-25.0, 25.0) if rot_deg is None else rot_deg
    tilt = rng.uniform(0.0, 25.0) if tilt_deg is None else tilt_deg
    az = rng.uniform(0, 2 * math.pi)
    yaw, pitch = tilt * math.cos(az), tilt * math.sin(az)
    margin = side * 0.75
    cx = rng.uniform(margin, CANVAS_W - margin) if centre is None else centre[0]
    cy = rng.uniform(margin, CANVAS_H - margin) if centre is None else centre[1]
    patch = _patch(mods, side)
    if occlusion > 0:
        cell = side / (n + 2 * QUIET)
        for _ in range(60):  # place one opaque patch that leaves the finder patterns alone
            ow = int(side * math.sqrt(occlusion) * rng.uniform(0.8, 1.25))
            oh = int(side * side * occlusion / max(ow, 1))
            ox, oy = rng.randint(0, max(0, side - ow)), rng.randint(0, max(0, side - oh))
            if protect_finders:
                zone = (QUIET + 8) * cell
                hit = [
                    ox < ex + zone and ox + ow > ex and oy < ey + zone and oy + oh > ey
                    for ex, ey in ((0, 0), (side - zone, 0), (0, side - zone))
                ]
                if any(hit):
                    continue
            patch[oy : oy + oh, ox : ox + ow] = rng.randint(90, 170)
            params["occ_box"] = (ox, oy, ow, oh)
            break
    dst = _homography(float(side), (cx, cy), rot_deg=rot, yaw_deg=yaw, pitch_deg=pitch)
    src = np.array([[0, 0], [side, 0], [side, side], [0, side]], dtype=np.float32)
    mat = cv2.getPerspectiveTransform(src, dst)
    patch3 = cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR)
    warped = cv2.warpPerspective(patch3, mat, (CANVAS_W, CANVAS_H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(
        np.full((side, side), 255, np.uint8), mat, (CANVAS_W, CANVAS_H), flags=cv2.INTER_LINEAR
    )
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    img = (warped.astype(np.float32) * alpha + img.astype(np.float32) * (1 - alpha)).astype(np.uint8)
    inner = np.array(
        [[QUIET, QUIET], [QUIET + n, QUIET], [QUIET + n, QUIET + n], [QUIET, QUIET + n]], dtype=np.float32
    ) * (side / (n + 2 * QUIET))
    symbol_quad = cv2.perspectiveTransform(inner[None], mat)[0]
    if glare > 0:
        _glare(img, rng, dst, glare)
    if blur_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur_sigma)
    if noise > 0:
        img = np.clip(img.astype(np.float32) + np_rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
    if jpeg_quality is not None:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
        if ok:
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    params.update(
        side=side,
        modules=n,
        module_px=round(side / (n + 2 * QUIET), 2),
        rot=round(rot, 1),
        tilt=round(tilt, 1),
        blur=blur_sigma,
        glare=glare,
        occlusion=occlusion,
        ec=ec,
    )
    return QrScene(img, kind, dst, symbol_quad, params)


RECIPES: list[dict[str, Any]] = [
    {"module_px": 5.0},  # clean, close
    {"module_px": 4.0, "tilt_deg": 25.0},
    {"module_px": 2.4},  # far
    {"module_px": 2.6, "tilt_deg": 15.0},
    {"module_px": 3.6, "blur_sigma": 1.2},
    {"module_px": 4.5, "blur_sigma": 1.6},
    {"module_px": 4.0, "glare": 0.5},
    {"module_px": 4.5, "occlusion": 0.12, "ec": "H"},
    {"module_px": 3.4, "tilt_deg": 25.0, "blur_sigma": 1.0},
    {"module_px": 3.5, "noise": 4.0, "jpeg_quality": 40},
    {"module_px": 3.2, "glare": 0.45, "tilt_deg": 20.0},
    {"module_px": 4.5, "occlusion": 0.15, "tilt_deg": 12.0, "ec": "H"},
]


def augmented_set(n_positive: int = 72, n_other: int = 24, n_none: int = 24, seed: int = 4242) -> list[QrScene]:
    """The acceptance set. Positives cycle through ``RECIPES`` (tilt up to 25 deg, blur sigma up to 1.6, partial
    glare, up to 15 % occlusion off the finder patterns, far codes); negatives are decodable non-payment codes
    (same recipes) and QR-free frames with finder-pattern and barcode look-alikes."""
    rng = random.Random(seed)
    out: list[QrScene] = []
    for i in range(n_positive):
        out.append(make_qr_scene("upi", rng.getrandbits(31), **RECIPES[i % len(RECIPES)]))
    for i in range(n_other):
        out.append(make_qr_scene("other", rng.getrandbits(31), **RECIPES[i % len(RECIPES)]))
    for _ in range(n_none):
        out.append(make_qr_scene("none", rng.getrandbits(31)))
    return out
