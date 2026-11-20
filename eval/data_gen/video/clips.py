"""Video clips for the SPECIMEN suite (T2.1): 5 s at 30 fps, 1280x720, ground truth quads per frame.

Each clip is one suite sprite (a card, plate, QR, signboard, screen, face scene ...) moving through a short
camera path: ``static`` (handheld jitter only: the flicker test), ``drift`` (slow pan and zoom) or ``wobble``
(tilt and zoom oscillation). Augmentation parameters come from ``augment.sample_params`` for the clip's level,
the JPEG pass is replaced by an x264 encode at crf 23 to 35 (spec 5.2).

Files per clip, under ``<out>/clips/<bucket>/``::

    <id>.mp4          H.264 yuv420p, 30 fps, no audio
    <id>.json         clip-level truth: motion, aug, crf, target attributes (number, value, script ...)
    <id>.truth.jsonl  one line per frame: targets / distractors / context quads with visible, in_frame_fraction,
                      min_side_px and occlusion_fraction (the same visibility rule as the images)

Targets keep their position in the sprite's target list, so line ``k`` entry ``j`` is always target ``j`` of
``<id>.json``. The manifest hash covers the raw rendered frames, not the encoder output.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from ..common.seeds import np_rng, py_rng
from .augment import apply_effects, sample_params
from .compose import (
    CANVAS,
    Placement,
    Sprite,
    choose_placement,
    convex,
    finalize,
    make_backdrop,
    place,
    placement_ok,
    projected_quad,
)

FPS = 30
DEFAULT_SECONDS = 5.0
MAX_TILT = 30.0
MOTIONS = ("static", "drift", "wobble")
# index -> (level, motion). Two clips per bucket give a clean static one (flicker) and a heavy moving one.
PLAN = (
    ("clean", "static"),
    ("heavy", "drift"),
    ("light", "wobble"),
    ("heavy", "static"),
    ("light", "drift"),
    ("clean", "wobble"),
)
CRF = {"clean": (23, 26), "light": (26, 30), "heavy": (30, 35)}
CLIP_BUCKETS = (
    "aadhaar",
    "pan",
    "upi_qr",
    "qr_negative",
    "plate",
    "signboard_devanagari",
    "signboard_tamil",
    "otp_screen",
    "game_bgmi",
    "game_freefire",
    "text_phone",
    "faces_enrolled",
)
_CLEAN_ATTR_DROP = ("quad", "visible", "in_frame_fraction", "min_side_px", "occlusion_fraction")


class ClipError(RuntimeError):
    pass


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def plan_for(index: int) -> tuple[str, str]:
    return PLAN[index % len(PLAN)]


def _pose(
    base: Placement,
    sprite_size: tuple[int, int],
    kind: str,
    u: float,
    amp: float,
    shape: dict[str, float],
) -> Placement:
    """Placement at normalised time `u` in [0, 1] for motion `kind` with amplitude scale `amp`."""
    cx, cy = base.center
    roll, yaw, pitch, width = base.roll, base.yaw, base.pitch, base.width_px
    s = math.sin(2 * math.pi * (shape["cycles"] * u + shape["phase"]))
    c = math.cos(2 * math.pi * (shape["cycles"] * u + shape["phase"]))
    if kind == "static":
        cx, cy = cx + 1.6 * s, cy + 1.2 * c  # handheld jitter, about a pixel and a half
    elif kind == "drift":
        cx += shape["dx"] * amp * (u - 0.5)
        cy += shape["dy"] * amp * (u - 0.5)
        width *= 1.0 + 0.22 * amp * shape["zoom"] * (u - 0.5)
        roll += 6.0 * amp * shape["zoom"] * (u - 0.5)
    else:  # wobble
        roll += 9.0 * amp * s
        yaw += 8.0 * amp * math.sin(2 * math.pi * shape["cycles"] * u + 1.3)
        pitch += 6.0 * amp * c
        width *= 1.0 + 0.07 * amp * s
        cx += shape["dx"] * 0.25 * amp * c
        cy += shape["dy"] * 0.25 * amp * s
    roll, yaw, pitch = (max(-MAX_TILT, min(MAX_TILT, v)) for v in (roll, yaw, pitch))
    dst = projected_quad(sprite_size, (cx, cy), width, roll, yaw, pitch)
    return Placement((cx, cy), width, roll, yaw, pitch, dst)


def _zoom_pose(u: float, kind: str, amp: float, shape: dict[str, float]) -> Placement:
    """Full-frame screen capture slowly zooming about the frame centre (clean screen clips)."""
    w, h = CANVAS
    s = math.sin(2 * math.pi * (shape["cycles"] * u + shape["phase"]))
    zoom = 1.0
    if kind == "drift":
        zoom = 1.0 + 0.10 * amp * u
    elif kind == "wobble":
        zoom = 1.0 + 0.05 * amp * (1 + s) / 2
    sh = 2.0 * s if kind != "static" else 0.0
    cx, cy = w / 2 + sh, h / 2
    dst = np.array(
        [
            [cx - w * zoom / 2, cy - h * zoom / 2],
            [cx + w * zoom / 2, cy - h * zoom / 2],
            [cx + w * zoom / 2, cy + h * zoom / 2],
            [cx - w * zoom / 2, cy + h * zoom / 2],
        ]
    )
    return Placement((cx, cy), w * zoom, 0.0, 0.0, 0.0, dst)


def _path(
    rng: random.Random,
    base: Placement,
    sprite_size: tuple[int, int],
    kind: str,
    n: int,
    identity: bool,
) -> tuple[list[Placement], float, dict[str, float]]:
    shape = {
        "dx": rng.choice((-1, 1)) * rng.uniform(0.06, 0.16) * CANVAS[0],
        "dy": rng.choice((-1, 1)) * rng.uniform(0.02, 0.07) * CANVAS[1],
        "zoom": rng.choice((-1, 1)),
        "cycles": rng.choice((1.0, 1.5, 2.0)),
        "phase": rng.random(),
    }
    for amp in (1.0, 0.6, 0.3, 0.0):
        us = [k / max(1, n - 1) for k in range(n)]
        if identity:
            poses = [_zoom_pose(u, kind, amp, shape) for u in us]
            return poses, amp, shape  # a zoom into a full frame capture needs no placement test
        poses = [_pose(base, sprite_size, kind, u, amp, shape) for u in us]
        if all(convex(p.dst) and placement_ok(p.dst) for p in poses):
            return poses, amp, shape
    return [base] * n, 0.0, shape


def _frame_record(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: rec[k] for k in ("category", *_CLEAN_ATTR_DROP)}


def render_clip(
    bucket: str, index: int, seed: int, faces_cache: str, seconds: float = DEFAULT_SECONDS
) -> tuple[Any, dict[str, Any]]:
    """Return (frame iterator, clip truth). The iterator yields ``(rgb frame, frame truth dict)``.

    The clip truth is complete only after the iterator is exhausted (``frame_sha256`` and ``n_frames`` are set
    at the end), so consume it fully before reading them.
    """
    from . import suite  # local import: suite imports nothing from here

    spec = suite.SPECS[bucket]
    level, kind = plan_for(index)
    rng = py_rng(seed, "clip", bucket, index)
    sprite: Sprite = spec.make(rng, index, suite.Ctx(faces_cache))
    identity = spec.mode == "screen" and level == "clean"
    params = sample_params(rng, level, spec.width, allow_tilt=not identity, allow_occlusion=not identity)
    params.jpeg_quality = 0  # x264 does the compression
    sh, sw = sprite.image.shape[:2]
    n = max(2, round(seconds * FPS))
    if identity:
        base = _zoom_pose(0.0, "static", 0.0, {"cycles": 1.0, "phase": 0.0})
    else:
        base = choose_placement(
            rng,
            (sw, sh),
            params.width_frac,
            params.roll,
            params.yaw,
            params.pitch,
            max_height_frac=spec.max_height_frac,
        )
    poses, amp, shape = _path(rng, base, (sw, sh), kind, n, identity)
    style = rng.choice(spec.backdrops) if spec.backdrops else None
    canvas, style = make_backdrop(seed, f"clip_{bucket}_{index}", style)
    crf = rng.randint(*CRF[level])
    truth: dict[str, Any] = {
        "category": bucket,
        "group": spec.group,
        "specimen": True,
        "level": level,
        "motion": {
            "kind": kind,
            "amplitude": round(amp, 2),
            "static_jitter_px": 1.6 if kind == "static" else 0,
        },
        "aug": params.as_dict(),
        "crf": crf,
        "fps": FPS,
        "n_frames": n,
        "size": list(CANVAS),
        "backdrop": style,
        "geometry": {"mode": "identity" if identity else "placed"},
        "sprite_meta": sprite.meta,
    }

    def frames() -> Any:
        digest = hashlib.sha256()
        for k, pl in enumerate(poses):
            placed = place(canvas, sprite, pl)
            img, occ = apply_effects(
                placed.image, placed.sprite_mask, params, np_rng(seed, "clipfx", bucket, index, k)
            )
            digest.update(np.ascontiguousarray(img).tobytes())
            rec = {
                "frame": k,
                "t": round(k / FPS, 4),
                "targets": finalize(placed.targets, occ),
                "distractors": finalize(placed.distractors, occ),
                "context": finalize(placed.context, occ),
            }
            if k == 0:  # attributes (number, value, script ...) live once in the clip truth
                for key in ("targets", "distractors", "context"):
                    truth[key] = [{a: v for a, v in r.items() if a not in _CLEAN_ATTR_DROP} for r in rec[key]]
            yield (
                img,
                {
                    key: ([_frame_record(r) for r in v] if isinstance(v, list) else v)
                    for key, v in rec.items()
                },
            )
        truth["frame_sha256"] = digest.hexdigest()
        main = [j for j, t in enumerate(truth["targets"]) if not t.get("aux")]
        truth["main_target_indices"] = main

    return frames(), truth


def encode_clip(frames: Any, path: Path, crf: int, ffmpeg: str) -> list[dict[str, Any]]:
    """Pipe raw RGB frames into x264. Returns the per-frame truth records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = CANVAS
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-g", "30", "-movflags", "+faststart", str(path),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    records: list[dict[str, Any]] = []
    try:
        assert proc.stdin is not None
        for img, rec in frames:
            proc.stdin.write(np.ascontiguousarray(img).tobytes())
            records.append(rec)
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise ClipError(f"ffmpeg failed: {err[-300:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return records


def clip_task(args: tuple[str, int, int, str | None, str, float]) -> dict[str, Any]:
    bucket, index, seed, out_dir, faces_cache, seconds = args
    frames, truth = render_clip(bucket, index, seed, faces_cache, seconds)
    clip_id = f"{bucket}_clip{index:02d}"
    rel = f"clips/{bucket}/{clip_id}"
    records: list[dict[str, Any]] = []
    if out_dir is not None:
        ff = ffmpeg_path()
        if ff is None:
            raise ClipError("ffmpeg not found on PATH")
        records = encode_clip(frames, Path(out_dir) / f"{rel}.mp4", truth["crf"], ff)
        root = Path(out_dir)
        (root / f"{rel}.truth.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records),
            encoding="utf-8",
        )
        (root / f"{rel}.json").write_text(
            json.dumps(truth, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        for _img, rec in frames:
            records.append(rec)
    visible = sum(
        1 for r in records for t in r["targets"] if t["visible"]
    )  # target-frames a detector must mask
    return {
        "id": clip_id,
        "bucket": bucket,
        "video": f"{rel}.mp4",
        "truth_file": f"{rel}.truth.jsonl",
        "meta_file": f"{rel}.json",
        "frame_sha256": truth["frame_sha256"],
        "level": truth["level"],
        "motion": truth["motion"]["kind"],
        "crf": truth["crf"],
        "n_frames": truth["n_frames"],
        "visible_target_frames": visible,
        "truth": {k: truth[k] for k in ("targets", "distractors", "main_target_indices")},
    }
