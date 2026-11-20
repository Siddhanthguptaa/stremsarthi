"""SPECIMEN video dataset builder v0: cards and QR posters placed on a 1280x720 desk image.

Output (``eval/data/generated/<name>/``): PNG per item plus ``manifest.json`` with ``seed``,
``generator_version`` and ground truth per item. The same (generator_version, seed, n) always gives the
same manifest hash; item pixels are hashed on the raw RGB array, not on the PNG bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from ...sender.stamp import STAMP_EXCLUSION
from ..common import specimen
from ..common.seeds import np_rng, py_rng
from .cards import render_aadhaar, render_pan
from .qr import render_qr_poster

GENERATOR_VERSION = "specimen-video-0.1"
CANVAS = (1280, 720)
KINDS = ("aadhaar", "pan", "upi_qr", "qr_negative")
DEFAULT_ROOT = Path("eval/data/generated")


@dataclass(frozen=True)
class Placed:
    image: np.ndarray  # RGB uint8 720x1280x3
    quad: tuple[tuple[float, float], ...]  # clockwise from top-left, canvas pixels


def backdrop(seed: int, name: str, size: tuple[int, int] = CANVAS) -> np.ndarray:
    """Plain desk-like background: vertical gradient plus fine noise (seeded)."""
    w, h = size
    g = np_rng(seed, "backdrop", name)
    top = np.array([96, 84, 70], dtype=np.float32) + g.integers(-12, 13, 3)
    bottom = np.array([140, 122, 100], dtype=np.float32) + g.integers(-12, 13, 3)
    ramp = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    img = top * (1 - ramp) + bottom * ramp
    img = np.broadcast_to(img, (h, w, 3)).copy()
    img += g.normal(0, 3.0, (h, w, 1)).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def paste(
    canvas: np.ndarray, sprite: np.ndarray, center: tuple[float, float], scale: float, angle: float
) -> Placed:
    """Warp `sprite` (scale, rotation in degrees) onto `canvas` and return the new image and quad."""
    sh, sw = sprite.shape[:2]
    m = cv2.getRotationMatrix2D((sw / 2, sh / 2), angle, scale)
    m[0, 2] += center[0] - sw / 2
    m[1, 2] += center[1] - sh / 2
    h, w = canvas.shape[:2]
    warped = cv2.warpAffine(sprite, m, (w, h), flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    mask = cv2.warpAffine(np.full((sh, sw), 255, np.uint8), m, (w, h), flags=cv2.INTER_LINEAR)
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    out = (canvas.astype(np.float32) * (1 - alpha) + warped.astype(np.float32) * alpha).astype(np.uint8)
    corners = np.array([[0, 0, 1], [sw, 0, 1], [sw, sh, 1], [0, sh, 1]], dtype=np.float64)
    quad = tuple((float(x), float(y)) for x, y in (m @ corners.T).T)
    return Placed(out, quad)


def _quad_ok(quad: tuple[tuple[float, float], ...], size: tuple[int, int] = CANVAS, margin: int = 8) -> bool:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    sx1, sy1, sx2, sy2 = STAMP_EXCLUSION
    inside = (
        min(xs) >= margin
        and min(ys) >= margin
        and max(xs) <= size[0] - margin
        and max(ys) <= size[1] - margin
    )
    overlaps_stamp = not (max(xs) < sx1 or min(xs) > sx2 or max(ys) < sy1 or min(ys) > sy2)
    return inside and not overlaps_stamp


def place_random(sprite: np.ndarray, seed: int, name: str, max_tilt: float = 12.0) -> Placed:
    """Place a sprite at a seeded position, scale and small tilt; never over the stamp region."""
    g = py_rng(seed, "place", name)
    sh, sw = sprite.shape[:2]
    for _ in range(200):
        target_w = g.uniform(0.28, 0.5) * CANVAS[0]
        scale = target_w / sw
        if sh * scale > CANVAS[1] * 0.85:
            scale = CANVAS[1] * 0.85 / sh
        angle = g.uniform(-max_tilt, max_tilt)
        cx = g.uniform(0.3, 0.7) * CANVAS[0]
        cy = g.uniform(0.4, 0.62) * CANVAS[1]
        # dry-run the geometry on a tiny canvas to test the constraints
        rad = math.radians(angle)
        half_w, half_h = sw * scale / 2, sh * scale / 2
        pts = []
        for dx, dy in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
            pts.append(
                (cx + dx * math.cos(rad) + dy * math.sin(rad), cy - dx * math.sin(rad) + dy * math.cos(rad))
            )
        if _quad_ok(tuple(pts)):
            return paste(backdrop(seed, name), sprite, (cx, cy), scale, angle)
    raise RuntimeError("could not place sprite away from the stamp region")


def build_item(kind: str, index: int, seed: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Render one dataset item. Returns the 1280x720 RGB image and its ground-truth dict."""
    name = f"{kind}_{index:04d}"
    rng = py_rng(seed, kind, index)
    truth: dict[str, Any] = {"specimen": True}
    if kind == "aadhaar":
        card = render_aadhaar(rng)
        sprite = card.image
        truth.update(
            number=card.number, number_digits=card.number.replace(" ", ""), number_bbox=card.number_bbox
        )
        truth["fields"] = card.fields
    elif kind == "pan":
        card = render_pan(rng)
        sprite = card.image
        truth.update(number=card.number, number_bbox=card.number_bbox, fields=card.fields)
    elif kind in ("upi_qr", "qr_negative"):
        payload = None if kind == "upi_qr" else rng.choice(specimen.QR_NEGATIVE_PAYLOADS)
        qr = render_qr_poster(rng, payload=payload, handle=rng.choice(specimen.UPI_HANDLES))
        sprite = qr.image
        truth.update(payload=qr.payload, upi_id=qr.upi_id, positive=qr.positive, qr_bbox=qr.qr_bbox)
    else:
        raise ValueError(f"unknown kind {kind!r}")
    placed = place_random(sprite, seed, name)
    truth["quad"] = [[round(x, 2), round(y, 2)] for x, y in placed.quad]
    truth["category"] = kind
    return placed.image, truth


def pixel_hash(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_dataset(
    out_dir: Path | None, seed: int, n_per_kind: int = 5, kinds: tuple[str, ...] = KINDS
) -> dict[str, Any]:
    """Generate the dataset. With `out_dir=None` nothing is written (used by determinism tests)."""
    items = []
    for kind in kinds:
        for i in range(n_per_kind):
            image, truth = build_item(kind, i, seed)
            fname = f"{kind}_{i:04d}.png"
            if out_dir is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                Image.fromarray(image).save(out_dir / fname, optimize=False)
            items.append(
                {"id": f"{kind}_{i:04d}", "file": fname, "pixel_sha256": pixel_hash(image), "truth": truth}
            )
    manifest = {
        "name": out_dir.name if out_dir is not None else "memory",
        "seed": seed,
        "generator_version": GENERATOR_VERSION,
        "canvas": list(CANVAS),
        "stamp_exclusion": list(STAMP_EXCLUSION),
        "n_per_kind": n_per_kind,
        "items": items,
    }
    # name is excluded from the hash so the same content hashes the same under any directory name
    manifest["manifest_sha256"] = manifest_hash({k: v for k, v in manifest.items() if k != "name"})
    if out_dir is not None:
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
