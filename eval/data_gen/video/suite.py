"""Video SPECIMEN suite (T2.1): every Phase 2 video category with augmentation and per-image ground truth.

``python -m eval.run gen-video --out eval/data/generated/video --seed 1234`` writes, per bucket, ``<id>.png``
and ``<id>.json`` plus a top-level ``manifest.json``. Everything is seeded from ``(GENERATOR_VERSION, seed)``:
each item draws its randomness from ``derive_seed(seed, bucket, index, ...)`` so items can be built in any
order and in parallel, and the manifest hash is identical for the same inputs.

Ground truth per item (``truth``)::

    category, group, specimen, level (clean|light|heavy), aug {...}, geometry {...}, backdrop,
    targets[]      {category, quad, visible, in_frame_fraction, min_side_px, occlusion_fraction, + attrs}
    distractors[]  same shape, things that must NOT be masked (hard negatives)
    context[]      whole-object quads (card, board, phone screen, vehicle, room panel)

A target is ``visible`` when at least 60 % of its quad is inside the frame and its shortest side is at least
24 px (docs/specs/eval.md 5.2). Targets with ``aux: true`` are sub-quads (digits box, OTP code) that are not
mask targets by themselves.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import random
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ...sender.stamp import STAMP_EXCLUSION
from ..common import fonts, identifiers, specimen
from ..common.seeds import np_rng, py_rng
from . import clips as clips_mod
from . import faces as faces_mod
from . import games, idcards, plates, scenes, screens, signboards, textpii
from .augment import apply_effects, level_for, sample_params
from .compose import (
    CANVAS,
    Placement,
    Sprite,
    SpriteTarget,
    choose_placement,
    finalize,
    make_backdrop,
    place,
    rect_quad,
)
from .qr import render_qr_poster

GENERATOR_VERSION = "specimen-video-1.2"  # 1.2: neg_scene bucket, longer face clips (other buckets unchanged)
DEFAULT_N = 40
DEFAULT_FACES_CACHE = "eval/data/generated/faces/src"


@dataclass(frozen=True)
class Ctx:
    faces_cache: str = DEFAULT_FACES_CACHE


@dataclass(frozen=True)
class Spec:
    name: str
    group: str
    make: Callable[[random.Random, int, Ctx], Sprite]
    width: tuple[float, float]  # distance range: object width as a fraction of the frame width
    mode: str = "object"  # object: always placed on a backdrop; screen: full-frame capture when clean
    backdrops: tuple[str, ...] | None = None
    max_height_frac: float = 0.9
    needs_faces: bool = False
    needs_raqm: bool = False


# ---- makers ------------------------------------------------------------------------------------------------
def _qr_sprite(rng: random.Random, positive: bool) -> Sprite:
    payload = None if positive else rng.choice(specimen.QR_NEGATIVE_PAYLOADS)
    if rng.random() < 0.6:
        qr = render_qr_poster(rng, payload=payload, handle=rng.choice(specimen.UPI_HANDLES))
        t = SpriteTarget(
            "upi_qr", rect_quad(qr.qr_bbox), {"payload": qr.payload, "upi_id": qr.upi_id, "carrier": "poster"}
        )
        h, w = qr.image.shape[:2]
        ctx = [SpriteTarget("poster", rect_quad((0, 0, w, h)), {})]
        if qr.positive:
            return Sprite(qr.image, [t], context=ctx, meta={"carrier": "poster", "positive": True})
        dist = SpriteTarget("non_upi_qr", t.quad, {"payload": qr.payload, "carrier": "poster"})
        return Sprite(
            qr.image, [], distractors=[dist], context=ctx, meta={"carrier": "poster", "positive": False}
        )
    s = screens.render_qr_phone(rng, payload=payload, landscape=rng.random() < 0.2)
    s.meta["carrier"] = "phone"
    return s


def _make_plate(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    text = identifiers.plate_bh(rng) if rng.random() < 0.2 else identifiers.plate_standard(rng)
    kind = rng.choices(("car", "bike", "truck"), weights=(5, 3, 2))[0]
    return plates.render_vehicle(rng, text, kind)


def _make_neg_plate(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    kind = rng.choices(("car", "bike", "truck"), weights=(5, 3, 2))[0]
    return plates.render_vehicle(rng, identifiers.plate_invalid(rng), kind, invalid_text=True)


def _make_otp(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    return screens.render_otp_screen(rng, landscape=rng.random() < 0.25)


def _make_neg_text(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    if i % 5 == 4:  # a board-like shape with no text at all
        return signboards.render_negative_board(rng)
    return textpii.render_text_screen(rng, ("phone", "pin", "upi_id", "email")[i % 4], negatives_only=True)


def _neg_card(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    sub = ("aadhaar_bad_checksum", "pan_malformed", "plain", "membership", "aadhaar_bad_checksum")[i % 5]
    return idcards.neg_card_sprite(rng, sub)


def _faces_pool(role: str | None = None, use: str | None = None) -> list[faces_mod.FaceSource]:
    return faces_mod.sources_for(use=use, role=role)


def _make_faces_multi(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    pool = _faces_pool(role="stranger")
    n_faces = rng.randint(2, 5)
    people, seen = [], set()
    for src in rng.sample(pool, len(pool)):
        if src.person_id not in seen:  # one portrait per person in a scene
            seen.add(src.person_id)
            people.append(src)
        if len(people) == n_faces:
            break
    return faces_mod.render_face_scene(rng, ctx.faces_cache, people)


def _make_faces_enrolled(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    enrolled = rng.choice(_faces_pool(use="test"))
    others = rng.sample(_faces_pool(role="stranger"), rng.choice((0, 1, 1, 2)))
    people = [enrolled, *others]
    rng.shuffle(people)
    return faces_mod.render_face_scene(rng, ctx.faces_cache, people, enrolled_ids=faces_mod.ENROLLED_PERSONS)


def _make_faces_neg(rng: random.Random, i: int, ctx: Ctx) -> Sprite:
    return faces_mod.render_face_negative(rng, ctx.faces_cache)


def _specs() -> dict[str, Spec]:
    specs: list[Spec] = [
        Spec("aadhaar", "card", lambda r, i, c: idcards.aadhaar_sprite(r), (0.2, 0.6)),
        Spec("pan", "card", lambda r, i, c: idcards.pan_sprite(r), (0.2, 0.6)),
        Spec("upi_qr", "qr", lambda r, i, c: _qr_sprite(r, True), (0.14, 0.4), max_height_frac=0.92),
        Spec("qr_negative", "qr", lambda r, i, c: _qr_sprite(r, False), (0.14, 0.4), max_height_frac=0.92),
        Spec("plate", "plate", _make_plate, (0.25, 0.7), backdrops=("street", "wall", "desk")),
    ]
    for sc in fonts.SCRIPTS:
        specs.append(
            Spec(
                f"signboard_{sc}",
                "signboard",
                (lambda s: lambda r, i, c: signboards.render_signboard(r, s))(sc),
                (0.25, 0.7),
                backdrops=("wall", "street", "cloth", "wood"),
                needs_raqm=sc != "latin",
            )
        )
    for kind in ("phone", "pin", "upi_id", "email"):
        specs.append(
            Spec(
                f"text_{kind}",
                "text_pii",
                (lambda k: lambda r, i, c: textpii.render_text_screen(r, k))(kind),
                (0.55, 1.0),
                mode="screen",
                backdrops=("dark_room", "desk", "wall"),
            )
        )
    specs.append(
        Spec(
            "otp_screen",
            "otp_screen",
            _make_otp,
            (0.14, 0.5),
            max_height_frac=0.92,
            backdrops=("desk", "wood", "cloth", "wall"),
        )
    )
    for style in games.STYLES:
        specs.append(
            Spec(
                f"game_{style}",
                "game",
                (lambda s: lambda r, i, c: games.render_lobby(r, s))(style),
                (
                    0.8,
                    1.0,
                ),  # player-ID text is small: farther than this it falls under the 24 px visible rule
                mode="screen",
                backdrops=("dark_room", "desk"),
            )
        )
    specs += [
        Spec(
            "faces_multi",
            "face",
            _make_faces_multi,
            (0.55, 1.0),
            mode="screen",
            backdrops=("dark_room", "desk"),
            needs_faces=True,
        ),
        Spec(
            "faces_enrolled",
            "face",
            _make_faces_enrolled,
            (0.55, 1.0),
            mode="screen",
            backdrops=("dark_room", "desk"),
            needs_faces=True,
        ),
        Spec(
            "faces_neg",
            "negative",
            _make_faces_neg,
            (0.55, 1.0),
            mode="screen",
            backdrops=("dark_room", "desk"),
            needs_faces=True,
        ),
        Spec("neg_card", "negative", _neg_card, (0.2, 0.6)),
        Spec("neg_plate", "negative", _make_neg_plate, (0.25, 0.7), backdrops=("street", "wall", "desk")),
        Spec(
            "neg_text",
            "negative",
            _make_neg_text,
            (0.55, 1.0),
            mode="screen",
            backdrops=("dark_room", "desk", "wall"),
            needs_raqm=True,
        ),
        Spec(
            "neg_screen",
            "negative",
            lambda r, i, c: screens.render_negative_screen(r, landscape=r.random() < 0.25),
            (0.14, 0.5),
            max_height_frac=0.92,
            backdrops=("desk", "wood", "cloth", "wall"),
        ),  # fmt: skip
        Spec(
            "neg_scene",
            "negative",
            lambda r, i, c: scenes.render_neg_scene(r, i),
            (0.55, 1.0),
            mode="screen",
            backdrops=("dark_room", "desk"),
        ),  # fmt: skip
        Spec(
            "neg_game",
            "negative",
            lambda r, i, c: games.render_lobby(r, ("bgmi", "freefire")[i % 2], with_credentials=False),
            (0.55, 1.0),
            mode="screen",
            backdrops=("dark_room", "desk"),
        ),  # fmt: skip
    ]
    return {s.name: s for s in specs}


SPECS = _specs()
BUCKETS = tuple(SPECS)


# ---- one item ----------------------------------------------------------------------------------------------
def _identity_placement(sprite: Sprite) -> Placement:
    w, h = CANVAS
    dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    return Placement((w / 2, h / 2), float(w), 0.0, 0.0, 0.0, dst)


def build_item(
    bucket: str, index: int, seed: int, ctx: Ctx | None = None
) -> tuple[np.ndarray, dict[str, Any]]:
    """Render one item: sprite, placement, backdrop, effects. Returns (RGB 1280x720 image, truth dict)."""
    ctx = ctx or Ctx()
    spec = SPECS[bucket]
    rng = py_rng(seed, bucket, index)
    g = np_rng(seed, bucket, index, "fx")
    level = level_for(index)
    sprite = spec.make(rng, index, ctx)
    identity = spec.mode == "screen" and (level == "clean" or (level == "light" and rng.random() < 0.5))
    params = sample_params(rng, level, spec.width, allow_tilt=not identity, allow_occlusion=not identity)
    sh, sw = sprite.image.shape[:2]
    if identity:
        pl = _identity_placement(sprite)
    else:
        pl = choose_placement(
            rng,
            (sw, sh),
            params.width_frac,
            params.roll,
            params.yaw,
            params.pitch,
            max_height_frac=spec.max_height_frac,
        )
    styles = spec.backdrops
    style = rng.choice(styles) if styles else None
    canvas, style = make_backdrop(seed, f"{bucket}_{index}", style)
    placed = place(canvas, sprite, pl)
    img, occ = apply_effects(placed.image, placed.sprite_mask, params, g)
    truth: dict[str, Any] = {
        "category": bucket,
        "group": spec.group,
        "specimen": True,
        "level": level,
        "aug": params.as_dict(),
        "geometry": {
            "mode": "identity" if identity else "placed",
            "width_px": round(pl.width_px, 1),
            "center": [round(pl.center[0], 1), round(pl.center[1], 1)],
            "roll": round(pl.roll, 2),
            "yaw": round(pl.yaw, 2),
            "pitch": round(pl.pitch, 2),
            "sprite_quad": [[round(float(x), 2), round(float(y), 2)] for x, y in pl.dst],
        },
        "backdrop": style,
        "sprite_meta": sprite.meta,
        "targets": finalize(placed.targets, occ),
        "distractors": finalize(placed.distractors, occ),
        "context": finalize(placed.context, occ),
    }
    truth["n_visible_targets"] = sum(1 for t in truth["targets"] if t["visible"] and not t.get("aux"))
    return img, truth


def pixel_hash(image: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _save_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image[..., ::-1], [cv2.IMWRITE_PNG_COMPRESSION, 1]):
        raise OSError(f"could not write {path}")


def _task(args: tuple[str, int, int, str | None, str]) -> dict[str, Any]:
    bucket, index, seed, out_dir, faces_cache = args
    img, truth = build_item(bucket, index, seed, Ctx(faces_cache))
    item_id = f"{bucket}_{index:04d}"
    item = {
        "id": item_id,
        "bucket": bucket,
        "file": f"{bucket}/{item_id}.png",
        "truth_file": f"{bucket}/{item_id}.json",
        "pixel_sha256": pixel_hash(img),
        "truth": truth,
    }
    if out_dir is not None:
        root = Path(out_dir)
        _save_png(root / item["file"], img)
        (root / item["truth_file"]).write_text(
            json.dumps(truth, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    return item


# ---- enrolment set -------------------------------------------------------------------------------------------
def build_enroll_sets(
    out_dir: Path | None, seed: int, faces_cache: str, frames_per_person: int = 24
) -> dict[str, Any]:
    """Enrolment frames for each enrolled person (>= 20, from the person's `enroll` portraits only)."""
    sets: dict[str, Any] = {}
    for pid in faces_mod.ENROLLED_PERSONS:
        srcs = faces_mod.sources_for(use="enroll", person_id=pid)
        frames = []
        for k in range(frames_per_person):
            src = srcs[k % len(srcs)]
            rng = py_rng(seed, "enroll", pid, k)
            img, quad = faces_mod.render_enroll_frame(rng, faces_cache, src)
            rel = f"faces_enroll/{pid}/frame_{k:02d}.png"
            if out_dir is not None:
                _save_png(out_dir / rel, img)
            frames.append(
                {
                    "file": rel,
                    "source": src.nasa_id,
                    "face_quad": [[round(x, 2), round(y, 2)] for x, y in quad],
                    "pixel_sha256": pixel_hash(img),
                }
            )
        sets[pid] = {"person_id": pid, "person": srcs[0].person, "frames": frames}
    return sets


# ---- dataset -----------------------------------------------------------------------------------------------
class EnvError(RuntimeError):
    """Something the generator needs is missing (fonts, raqm, face portraits). Nothing wrong with the code."""


def build_suite(
    out_dir: Path | None,
    seed: int,
    n_per_bucket: int = DEFAULT_N,
    buckets: tuple[str, ...] | None = None,
    workers: int | None = None,
    faces_cache: str = DEFAULT_FACES_CACHE,
    fetch: bool = True,
    clips_per_bucket: int = 0,
    clip_seconds: float = clips_mod.DEFAULT_SECONDS,
    clip_overrides: dict[str, tuple[int, float]] | None = None,
) -> dict[str, Any]:
    """Generate the suite. With `out_dir=None` nothing is written (used by determinism tests).

    `clips_per_bucket` > 0 also renders 30 fps clips (``clips.py``) for the buckets in ``CLIP_BUCKETS``.
    `clip_overrides` maps a clip bucket to ``(clips, seconds)`` when it needs more or longer clips than the rest
    (the face buckets: the allowlisted-face metric needs a warm-up of about a second plus settled frames).
    """
    t0 = time.monotonic()
    wanted = tuple(buckets) if buckets else BUCKETS
    unknown = [b for b in wanted if b not in SPECS]
    if unknown:
        raise ValueError(f"unknown buckets {unknown}; known: {list(BUCKETS)}")
    if any(SPECS[b].needs_raqm for b in wanted) and not fonts.raqm_available():
        raise EnvError("Pillow was built without libraqm: Indic signboards and Hindi text cannot be shaped")
    try:
        fonts.ensure_fonts()
    except fonts.FontError as exc:
        raise EnvError(f"fonts: {exc}") from exc
    if any(SPECS[b].needs_faces for b in wanted):
        problems = (
            faces_mod.fetch_sources(Path(faces_cache))
            if fetch
            else faces_mod.cache_problems(Path(faces_cache))
        )
        if problems:
            raise EnvError("face portraits unavailable: " + "; ".join(problems[:3]))
    tasks = [
        (b, i, seed, str(out_dir) if out_dir else None, faces_cache)
        for b in wanted
        for i in range(n_per_bucket)
    ]
    workers = workers if workers is not None else min(6, os.cpu_count() or 1)
    clip_buckets = [b for b in wanted if b in clips_mod.CLIP_BUCKETS] if clips_per_bucket > 0 else []
    ov = clip_overrides or {}
    clip_tasks = [
        (b, i, seed, str(out_dir) if out_dir else None, faces_cache, ov.get(b, (0, clip_seconds))[1])
        for b in clip_buckets
        for i in range(ov.get(b, (clips_per_bucket, 0.0))[0])
    ]
    if clip_tasks and out_dir is not None and clips_mod.ffmpeg_path() is None:
        raise EnvError("ffmpeg not found on PATH: clips cannot be encoded")
    clip_items: list[dict[str, Any]] = []
    if workers <= 1 or len(tasks) < 8:
        items = [_task(t) for t in tasks]
        clip_items = [clips_mod.clip_task(t) for t in clip_tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as ex:
            items = list(ex.map(_task, tasks, chunksize=4))
            clip_items = list(ex.map(clips_mod.clip_task, clip_tasks, chunksize=1))
    manifest: dict[str, Any] = {
        "name": out_dir.name if out_dir is not None else "memory",
        "seed": seed,
        "generator_version": GENERATOR_VERSION,
        "canvas": list(CANVAS),
        "stamp_exclusion": list(STAMP_EXCLUSION),
        "n_per_bucket": n_per_bucket,
        "buckets": {b: {"group": SPECS[b].group, "count": n_per_bucket} for b in wanted},
        "levels": {"clean": 0.2, "light": 0.4, "heavy": 0.4},
        "items": items,
    }
    if clip_items:
        manifest["clips"] = {
            "fps": clips_mod.FPS,
            "seconds": clip_seconds,
            "per_bucket": clips_per_bucket,
            "items": clip_items,
        }
        if ov:  # only buckets whose clip count or length differs from the rest
            manifest["clips"]["by_bucket"] = {
                b: {"clips": n, "seconds": sec} for b, (n, sec) in sorted(ov.items()) if b in clip_buckets
            }
    if any(SPECS[b].needs_faces for b in wanted):
        manifest["enroll_sets"] = build_enroll_sets(out_dir, seed, faces_cache)
        manifest["face_sources"] = [
            {
                "nasa_id": s.nasa_id,
                "person_id": s.person_id,
                "role": s.role,
                "use": s.use,
                "sha256": s.sha256,
                "licence": "public domain (NASA)",
            }
            for s in faces_mod.load_sources()
        ]
    manifest["manifest_sha256"] = manifest_hash({k: v for k, v in manifest.items() if k != "name"})
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        attr = Path(faces_cache).parent / "ATTRIBUTION.md"
        if attr.exists():
            (out_dir / "ATTRIBUTION.md").write_text(attr.read_text(encoding="utf-8"), encoding="utf-8")
    manifest["_seconds"] = round(time.monotonic() - t0, 1)  # not part of the hash or the file
    return manifest


# ---- in-place refresh ----------------------------------------------------------------------------------------
def refresh_dataset(
    out_dir: Path,
    image_buckets: tuple[str, ...] = (),
    clip_overrides: dict[str, tuple[int, float]] | None = None,
    faces_cache: str = DEFAULT_FACES_CACHE,
    fetch: bool = True,
    workers: int | None = None,
) -> dict[str, Any]:
    """Re-render only some buckets of an existing dataset and patch its manifest, leaving every other file untouched.

    ``image_buckets``: buckets whose stills are (re)rendered (a new bucket is added). ``clip_overrides``: clip buckets
    re-rendered with ``(clips, seconds)``. Items are rendered from the same ``(seed, bucket, index)`` derivation as a
    full run, so the result equals a full regeneration; the manifest hash and ``generator_version`` are updated.
    """
    path = out_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    seed, n = int(manifest["seed"]), int(manifest["n_per_bucket"])
    ov = dict(clip_overrides or {})
    unknown = [b for b in (*image_buckets, *ov) if b not in SPECS]
    if unknown:
        raise ValueError(f"unknown buckets {unknown}")
    if any(SPECS[b].needs_raqm for b in image_buckets) and not fonts.raqm_available():
        raise EnvError("Pillow was built without libraqm")
    fonts.ensure_fonts()
    if any(SPECS[b].needs_faces for b in (*image_buckets, *ov)):
        problems = (
            faces_mod.fetch_sources(Path(faces_cache))
            if fetch
            else faces_mod.cache_problems(Path(faces_cache))
        )
        if problems:
            raise EnvError("face portraits unavailable: " + "; ".join(problems[:3]))
    workers = workers if workers is not None else min(6, os.cpu_count() or 1)
    tasks = [(b, i, seed, str(out_dir), faces_cache) for b in image_buckets for i in range(n)]
    clip_tasks = [
        (b, i, seed, str(out_dir), faces_cache, sec) for b, (count, sec) in ov.items() for i in range(count)
    ]
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork")) as ex:
        items = list(ex.map(_task, tasks, chunksize=4)) if tasks else []
        clip_items = list(ex.map(clips_mod.clip_task, clip_tasks, chunksize=1)) if clip_tasks else []
    order = {b: k for k, b in enumerate(BUCKETS)}
    if items:
        keep = [it for it in manifest["items"] if it["bucket"] not in image_buckets]
        manifest["items"] = sorted(keep + items, key=lambda it: (order[it["bucket"]], it["id"]))
        for b in image_buckets:
            manifest["buckets"][b] = {"group": SPECS[b].group, "count": n}
    if clip_items:
        clips = manifest["clips"]
        keep_c = [c for c in clips["items"] if c["bucket"] not in ov]
        clips["items"] = sorted(keep_c + clip_items, key=lambda c: (order[c["bucket"]], c["id"]))
        by = dict(clips.get("by_bucket") or {})
        by.update({b: {"clips": cnt, "seconds": sec} for b, (cnt, sec) in ov.items()})
        clips["by_bucket"] = dict(sorted(by.items()))
    manifest["generator_version"] = GENERATOR_VERSION
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = manifest_hash({k: v for k, v in manifest.items() if k != "name"})
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
