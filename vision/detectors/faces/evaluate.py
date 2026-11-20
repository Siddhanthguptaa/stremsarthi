"""T2.6 acceptance runner for the faces detector (CPU, no network).

``python -m vision.detectors.faces.evaluate --photos .tmp/facedata/photos [--json out.json]``

Input: a directory of ``<person>__<id>.jpg`` portraits (the pinned NASA set from ``vision/tests/faces_testdata.py``,
or the T2.1 face sets). Output: masking and visibility rates per scene group and role, genuine/impostor cosine
statistics with a sweep of ``face_match_cos``, and the detector time per run.

Method. Portraits are cut around the face and composed into 1280x720 scenes (one to three faces moving like people
do, with noise, brightness drift and JPEG artefacts), then fed through the real ``FacesDetector`` and
``MaskPlanner`` at the dev stride (a detection run every 0.1 s). Ground truth is the face box of the portrait (found
once by YuNet on the clean photo) carried through the same affine transform as the pixels. Enrolled people are
enrolled from DIFFERENT photos than the ones shown in the scenes (held-out test).

Per ground-truth face and run, using the planner's ``MaskRequest`` quads (so holds and padding count):
* a face that must be masked (a stranger, or anybody when the allowlist is empty): a request covers at least 50 %
  of its box;
* an enrolled face: no request covers more than 20 % of its box and the detector said ``allow``.
Only faces at least 50 % inside the frame count. "steady" skips the first ``WARMUP_RUNS`` runs of every clip (the
confirmation window: a new track is masked until 5 of 7 samples match, by design); "all" keeps them.

Limits, repeated in the output: astronaut portraits are frontal, well lit and not representative of Indian
streamers on phone cameras; profile faces are simulated by squeezing the portrait (no real profiles); hard cases
are reported separately. Tune ``face_match_cos`` on consented teammate footage before trusting any number here.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from contracts import CANVAS_H, CANVAS_W, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision.planner import MaskPlanner

from .core import FaceModels, faces_from_rows, l2_normalise, load_models
from .detector import FacesDetector
from .enroll import Enroller

STRIDE = 3
FPS = 30
RUNS = 100  # 10 s of stream at the dev stride
WARMUP_RUNS = 8
TILE_FACE_W = 240  # a tile is rendered at this face width, then scaled per scene
ENROL_PHOTOS = 2  # the first photos of an enrolled person are used to enrol, the rest are shown in scenes
ENROLLED = ("menon", "chari", "barron")
LIMITS = (
    "NASA astronaut portraits: frontal, well lit, not representative of Indian streamers on phone cameras; "
    "'turned' is a squeezed portrait, not a real profile; tune face_match_cos on consented teammate footage."
)


# --------------------------------------------------------------------------- portraits
@dataclass(slots=True)
class Tile:
    person: str
    name: str
    image: np.ndarray  # BGR crop around the face, face width = TILE_FACE_W
    box: tuple[float, float, float, float]  # face box in tile coordinates


def load_people(photos: Path, models: FaceModels) -> dict[str, list[Tile]]:
    people: dict[str, list[Tile]] = {}
    for path in sorted(photos.glob("*__*.jpg")):
        img = cv2.imread(str(path))
        if img is None:
            continue
        h, w = img.shape[:2]
        s = min(1.0, 640 / max(h, w))
        small = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        faces = sorted(
            faces_from_rows(models.detect(small, 0.6), w / small.shape[1], h / small.shape[0]),
            key=lambda f: -f.w * f.h,
        )
        if not faces or min(faces[0].w, faces[0].h) < 70:
            continue
        if len(faces) > 1 and faces[1].w * faces[1].h > 0.25 * faces[0].w * faces[0].h:
            continue  # a group shot: the ground truth would be ambiguous
        f = faces[0]
        k = TILE_FACE_W / f.w
        cx, cy = (f.x1 + f.x2) / 2, (f.y1 + f.y2) / 2
        x0, y0 = int(cx - 1.8 * f.w), int(cy - 1.9 * f.w)
        x1, y1 = int(cx + 1.8 * f.w), int(cy + 2.4 * f.w)
        pad = max(0, -x0, -y0, x1 - w, y1 - h) + 2
        padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        crop = padded[y0 + pad : y1 + pad, x0 + pad : x1 + pad]
        tile = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
        box = ((f.x1 - x0) * k, (f.y1 - y0) * k, (f.x2 - x0) * k, (f.y2 - y0) * k)
        person, name = path.stem.split("__", 1)
        people.setdefault(person, []).append(Tile(person, name, tile, box))
    return people


# --------------------------------------------------------------------------- scenes
@dataclass(slots=True)
class Placement:
    tile: Tile
    cx: float  # face centre on the canvas
    cy: float
    width: float  # face width in pixels
    enrolled: bool  # this person is on the allowlist (and must stay visible when the clip has one)
    angle: float = 0.0
    squeeze: float = 1.0  # < 1 narrows the face (simulated turn)
    phase: float = 0.0


@dataclass(slots=True)
class Clip:
    name: str
    group: str
    placements: list[Placement]
    allowlist: tuple[str, ...]
    seed: int
    brightness: float = 1.0


def _background(rng: np.random.Generator, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    base = np.linspace(rng.uniform(90, 130), rng.uniform(110, 160), w, dtype=np.float32)[None, :, None]
    tint = rng.uniform(0.9, 1.1, 3).astype(np.float32)[None, None, :]
    return np.clip(np.broadcast_to(base * tint, (h, w, 3)), 0, 255).astype(np.uint8).copy()


def render(
    placements: list[Placement], brightness: float, run: int, rng: np.random.Generator, bg: np.ndarray
) -> tuple[np.ndarray, list[tuple[Placement, tuple[float, float, float, float], float]]]:
    """One frame plus ``(placement, ground-truth face box clipped to the canvas, visible fraction)`` per face."""
    canvas = bg.copy()
    h, w = canvas.shape[:2]
    t = run / 10.0
    truth = []
    for p in placements:
        cx = p.cx + 9.0 * np.sin(2 * np.pi * 0.35 * t + p.phase)
        cy = p.cy + 5.0 * np.sin(2 * np.pi * 0.5 * t + 2 * p.phase)
        k = p.width * (1.0 + 0.03 * np.sin(2 * np.pi * 0.2 * t + p.phase)) / TILE_FACE_W
        x1, y1, x2, y2 = p.tile.box
        fc = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
        a = np.deg2rad(p.angle)
        rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        lin = rot @ np.diag([k * p.squeeze, k])
        m = np.hstack([lin, (np.array([cx, cy]) - lin @ fc)[:, None]])
        cv2.warpAffine(
            p.tile.image, m, (w, h), dst=canvas, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT
        )
        corners = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]) @ lin.T + m[:, 2]
        bx1, by1 = corners.min(axis=0)
        bx2, by2 = corners.max(axis=0)
        full = max(1e-6, (bx2 - bx1) * (by2 - by1))
        cb = (max(0.0, bx1), max(0.0, by1), min(float(w), bx2), min(float(h), by2))
        vis = max(0.0, cb[2] - cb[0]) * max(0.0, cb[3] - cb[1]) / full
        truth.append((p, cb, vis))
    img = canvas.astype(np.float32) * brightness * (1.0 + 0.03 * np.sin(0.7 * t))
    img += rng.normal(0.0, 2.0, img.shape).astype(np.float32)
    out = np.clip(img, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])  # h264-like blocking
    return (cv2.imdecode(enc, cv2.IMREAD_COLOR) if ok else out), truth


def build_clips(people: dict[str, list[Tile]]) -> list[Clip]:
    enrolled = [p for p in ENROLLED if p in people and len(people[p]) > ENROL_PHOTOS]
    strangers = sorted(p for p in people if p not in enrolled)
    held = {p: people[p][ENROL_PHOTOS:] for p in enrolled}
    clips: list[Clip] = []

    def add(
        name: str,
        group: str,
        faces: list[tuple[Tile, bool]],
        xs: list[float],
        width: float,
        *,
        allowlist: bool = True,
        brightness: float = 1.0,
        angle: float = 0.0,
        squeeze: float = 1.0,
    ) -> None:
        seed = 1000 + len(clips)
        placements = [
            Placement(tile, xs[i], CANVAS_H * 0.48, width, is_enrolled, angle, squeeze, phase=i * 1.3 + seed % 7)
            for i, (tile, is_enrolled) in enumerate(faces)
        ]
        clips.append(Clip(name, group, placements, tuple(enrolled) if allowlist else (), seed, brightness))

    mid = [CANVAS_W / 2]
    pair = [CANVAS_W * 0.36, CANVAS_W * 0.64]
    # standard scenes: upright, lit, 150-230 px faces
    for p in enrolled:
        for tile in held[p]:
            add(f"enrolled_alone/{p}/{tile.name}", "enrolled_alone", [(tile, True)], mid, 230)
    for s in strangers:
        for tile in people[s][:3]:
            add(f"stranger_alone/{s}/{tile.name}", "stranger_alone", [(tile, False)], mid, 230)
    for i, p in enumerate(enrolled):
        for j, tile in enumerate(held[p][:2]):
            s = strangers[(i + j) % len(strangers)]
            other = people[s][j % len(people[s])]
            faces = [(tile, True), (other, False)] if j % 2 == 0 else [(other, False), (tile, True)]
            add(f"enrolled_next_to_stranger/{p}+{s}/{j}", "enrolled_next_to_stranger", faces, pair, 190)
    for i, p in enumerate(enrolled):
        a, b = strangers[i % len(strangers)], strangers[(i + 2) % len(strangers)]
        trio = [(people[a][0], False), (held[p][0], True), (people[b][-1], False)]
        add(f"enrolled_among_two_strangers/{p}", "enrolled_among_two_strangers", trio, [320, 640, 960], 150)
    for i, p in enumerate(enrolled):  # nobody is allowlisted: everybody is masked, the enrolled person too
        s = strangers[i % len(strangers)]
        add(f"empty_allowlist/{p}+{s}", "empty_allowlist", [(held[p][0], True), (people[s][0], False)], pair, 190, allowlist=False)
    # hard cases: one face per clip
    hard: dict[str, dict[str, Any]] = {
        "small": {"width": 62},
        "tilt": {"width": 200, "angle": 25.0},
        "turned": {"width": 200, "squeeze": 0.62},
        "partial": {"width": 220},  # about 40 % of the face is outside the frame
        "lowlight": {"width": 220, "brightness": 0.33},
    }
    for tag, kw in hard.items():
        x = [CANVAS_W - 0.2 * kw["width"]] if tag == "partial" else mid
        for p in enrolled:
            add(f"hard_{tag}/enrolled/{p}", f"hard_{tag}", [(held[p][0], True)], x, **kw)
        for s in strangers[:3]:
            add(f"hard_{tag}/stranger/{s}", f"hard_{tag}", [(people[s][0], False)], x, **kw)
    return clips


# --------------------------------------------------------------------------- running
class _Allow:
    def __init__(self, entries: list[tuple[str, tuple[float, ...]]]) -> None:
        self._entries = tuple(entries)
        self.version = 1

    def embeddings(self) -> tuple[tuple[str, Any], ...]:
        return self._entries


class _Res:
    def __init__(self, allow: _Allow, config: Config) -> None:
        self.allowlist = allow
        self.config = config
        self.clock = time.monotonic

    def model_path(self, model_id: str) -> Path:
        from models.prepare import DEFAULT_CACHE, verified_path

        return verified_path(model_id, DEFAULT_CACHE)[0]


def make_detector(config: Config, allow: _Allow, *, async_embed: bool, **params: Any) -> FacesDetector:
    det = FacesDetector()
    cfg = DetectorConfig(
        categories=config.categories, device=Device.CPU, params={"embed_async": async_embed, **params}
    )
    det.configure(cfg, _Res(allow, config))  # type: ignore[arg-type]
    det.warmup()
    return det


def enrol_person(enroller: Enroller, tiles: list[Tile], rng: np.random.Generator) -> tuple[float, ...]:
    """Enrol from webcam-like frames (640x480, jitter in scale, position and tilt) of the enrolment photos."""
    frames = []
    for i in range(8):
        tile = tiles[i % ENROL_PHOTOS]
        bg = _background(rng, (640, 480))
        place = Placement(tile, 320, 230, float(rng.uniform(150, 210)), True, float(rng.uniform(-6, 6)))
        frames.append(render([place], 1.0, i * 7, rng, bg)[0])
    return enroller.enrol(frames, "eval")[1]


@dataclass(slots=True)
class Tally:
    runs_all: int = 0
    ok_all: int = 0
    runs_steady: int = 0
    ok_steady: int = 0
    stranger_allowed: int = 0  # face runs where a face that must be masked carried allow=True

    def add(self, ok: bool, steady: bool) -> None:
        self.runs_all += 1
        self.ok_all += ok
        if steady:
            self.runs_steady += 1
            self.ok_steady += ok


def _coverage(gt: tuple[float, float, float, float], boxes: list[tuple[float, float, float, float]]) -> float:
    area = max(1e-6, (gt[2] - gt[0]) * (gt[3] - gt[1]))
    best = 0.0
    for b in boxes:
        iw = max(0.0, min(gt[2], b[2]) - max(gt[0], b[0]))
        ih = max(0.0, min(gt[3], b[3]) - max(gt[1], b[1]))
        best = max(best, iw * ih / area)
    return best


def run_clip(clip: Clip, det: FacesDetector, config: Config, tallies: dict[str, Tally], times: list[float]) -> None:
    rng = np.random.default_rng(clip.seed)
    bg = _background(rng, (CANVAS_W, CANVAS_H))
    planner = MaskPlanner(Lane.FAST, config)
    for run in range(RUNS):
        image, truth = render(clip.placements, clip.brightness, run, rng, bg)
        seq = run * STRIDE
        frame = Frame(seq, seq / FPS, 0.0, image)
        t0 = time.perf_counter()
        dets = det.detect(frame, DetectContext(lane=Lane.FAST, categories=config.categories, stride=STRIDE))
        times.append(time.perf_counter() - t0)
        requests, _aux = planner.plan(frame.pts, dets)
        boxes = [r.quads[0].bbox() for r in requests]
        for p, gt, vis in truth:
            if vis < 0.5:
                continue
            cov = _coverage(gt, boxes)
            allowed = any(d.allow and _coverage(gt, [d.quad.bbox()]) >= 0.5 for d in dets)
            must_show = p.enrolled and bool(clip.allowlist)
            role = "visible" if must_show else "masked"
            tally = tallies.setdefault(f"{clip.group}:{role}", Tally())
            if must_show:
                tally.add(cov <= 0.2 and allowed, run >= WARMUP_RUNS)
            else:
                tally.stranger_allowed += int(allowed)
                tally.add(cov >= 0.5, run >= WARMUP_RUNS)


def cosine_stats(people: dict[str, list[Tile]], models: FaceModels, enrolled: dict[str, tuple[float, ...]]) -> dict[str, Any]:
    """Cosine of every held-out photo against each enrolled embedding, and a sweep of ``face_match_cos``."""
    genuine: list[float] = []
    impostor: list[float] = []
    for person, tiles in people.items():
        for tile in tiles[ENROL_PHOTOS:] if person in enrolled else tiles:
            faces = faces_from_rows(models.detect(tile.image, 0.6))
            if not faces:
                continue
            e = models.embed(tile.image, max(faces, key=lambda f: f.w * f.h).row)
            for who, vec in enrolled.items():
                c = float(e @ l2_normalise(np.asarray(vec)))
                (genuine if who == person else impostor).append(c)
    g, i = np.array(genuine), np.array(impostor)
    sweep = {
        f"{t:.3f}": {"genuine_pass": round(float((g >= t).mean()), 3), "impostor_pass": round(float((i >= t).mean()), 4)}
        for t in (0.30, 0.363, 0.40, 0.45, 0.50)
    }
    return {
        "genuine": {"n": len(g), "min": round(float(g.min()), 3), "p5": round(float(np.percentile(g, 5)), 3), "median": round(float(np.median(g)), 3)},
        "impostor": {"n": len(i), "max": round(float(i.max()), 3), "p99": round(float(np.percentile(i, 99)), 3), "median": round(float(np.median(i)), 3)},
        "sweep": sweep,
    }


def _pool(tallies: dict[str, Tally], keys: list[str]) -> dict[str, Any]:
    sel = [tallies[k] for k in keys if k in tallies]
    out: dict[str, Any] = {"face_runs": sum(t.runs_all for t in sel)}
    for label, runs, ok in (("all", "runs_all", "ok_all"), ("steady", "runs_steady", "ok_steady")):
        n, k = sum(getattr(t, runs) for t in sel), sum(getattr(t, ok) for t in sel)
        out[label] = round(k / n, 4) if n else None
    out["stranger_runs_with_allow_true"] = sum(t.stranger_allowed for t in sel)
    return out


def evaluate(photos: Path, *, async_embed: bool = False, match_cos: float | None = None) -> dict[str, Any]:
    cv2.setNumThreads(2)  # what the engine uses (engine.cv_threads)
    config = Config(profile="dev", _env_file=None)
    models = load_models()
    models.warmup()
    people = load_people(photos, models)
    enroller = Enroller(backend=models)
    rng = np.random.default_rng(7)
    enrolled = {
        p: enrol_person(enroller, people[p], rng) for p in ENROLLED if p in people and len(people[p]) > ENROL_PHOTOS
    }
    clips = build_clips(people)
    params = {} if match_cos is None else {"face_match_cos": match_cos}
    tallies: dict[str, Tally] = {}
    times: list[float] = []
    for clip in clips:
        det = make_detector(config, _Allow([(p, enrolled[p]) for p in clip.allowlist]), async_embed=async_embed, **params)
        run_clip(clip, det, config, tallies, times)
        det.close()
    standard = [g for g in tallies if not g.startswith("hard_")]
    summary = {
        "non_allowlisted_masked_standard": _pool(tallies, [g for g in standard if g.endswith(":masked")]),
        "allowlisted_visible_standard": _pool(tallies, [g for g in standard if g.endswith(":visible")]),
        "hard_masked": _pool(tallies, [g for g in tallies if g.startswith("hard_") and g.endswith(":masked")]),
        "hard_visible": _pool(tallies, [g for g in tallies if g.startswith("hard_") and g.endswith(":visible")]),
        "clips": len(clips),
        "people": len(people),
        "unique_photos_shown": len({(p.tile.person, p.tile.name) for c in clips for p in c.placements}),
        "enrolled": sorted(enrolled),
        "face_match_cos": match_cos if match_cos is not None else config.vision.face_match_cos,
        "detect_ms": {
            "mode": "worker-thread embedding (lane time)" if async_embed else "inline embedding (worst case)",
            "p50": round(float(np.percentile(times, 50) * 1e3), 1),
            "p95": round(float(np.percentile(times, 95) * 1e3), 1),
            "max": round(float(max(times) * 1e3), 1),
            "n": len(times),
        },
        "cosine": cosine_stats(people, models, enrolled),
        "limits": LIMITS,
    }
    groups = {g: _pool(tallies, [g]) for g in sorted(tallies)}
    return {"summary": summary, "groups": groups}


def main() -> None:
    ap = argparse.ArgumentParser(description="T2.6 faces acceptance run")
    ap.add_argument("--photos", type=Path, default=Path(".tmp/facedata/photos"))
    ap.add_argument("--json", type=Path)
    ap.add_argument("--async-embed", action="store_true", help="embed on the worker thread (lane-time numbers)")
    ap.add_argument("--match-cos", type=float)
    args = ap.parse_args()
    result = evaluate(args.photos, async_embed=args.async_embed, match_cos=args.match_cos)
    text = json.dumps(result, indent=1)
    print(text)
    if args.json:
        args.json.write_text(text)


if __name__ == "__main__":
    main()
