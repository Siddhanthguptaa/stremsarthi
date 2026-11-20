"""Face scenes from public-domain NASA portraits: multi-face grids, rooms, an enrolled-person set, negatives.

Only openly licensed portraits are used (see ``faces_sources.json``: NASA image library id, source URL,
licence, credit, date, SHA-256 and a face box for every file). No private people, no scraped photos. The
portraits are fetched on demand into ``eval/data/generated/faces/src`` (gitignored) and verified by hash;
``ATTRIBUTION.md`` is written beside them. Face boxes come from OpenCV YuNet (score >= 0.6, exactly one face
per source) and were checked on a contact sheet; they are approximate ground truth, not hand annotation.
"""

from __future__ import annotations

import hashlib
import json
import random
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..common.fonts import load_font
from .compose import CANVAS, Sprite, SpriteTarget, backdrop, rect_quad
from .render import silhouette

SOURCES_PATH = Path(__file__).with_name("faces_sources.json")
DEFAULT_CACHE = Path("eval/data/generated/faces/src")
LICENCE = (
    "Public domain. NASA media are generally not subject to copyright "
    "(https://www.nasa.gov/nasa-brand-center/images-and-media/); official portraits are US Government works."
)
ENROLLED_PERSONS = ("P01", "P02", "P03")


@dataclass(frozen=True)
class FaceSource:
    nasa_id: str
    person_id: str
    person: str
    role: str  # enrolled | stranger
    use: str  # enroll | test | stranger
    url: str
    page_url: str
    sha256: str
    size: tuple[int, int]
    box: tuple[float, float, float, float]  # x, y, w, h in source pixels
    credit: str
    date: str
    title: str
    licence: str = LICENCE

    @property
    def filename(self) -> str:
        return f"{self.nasa_id}.jpg"


@lru_cache(maxsize=1)
def load_sources() -> tuple[FaceSource, ...]:
    rows = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    out = []
    for r in rows:
        out.append(
            FaceSource(
                nasa_id=r["nasa_id"],
                person_id=r["person_id"],
                person=r["person"],
                role=r["role"],
                use=r["use"],
                url=r["url"],
                page_url=f"https://images.nasa.gov/details/{r['nasa_id']}",
                sha256=r["sha256"],
                size=(r["size"][0], r["size"][1]),
                box=tuple(r["box"]),  # type: ignore[arg-type]
                credit=r["credit"],
                date=r["date"],
                title=r["title"],
            )
        )
    return tuple(out)


def sources_for(
    use: str | None = None, person_id: str | None = None, role: str | None = None
) -> list[FaceSource]:
    return [
        s
        for s in load_sources()
        if (use is None or s.use == use)
        and (person_id is None or s.person_id == person_id)
        and (role is None or s.role == role)
    ]


def provenance_problems(sources: tuple[FaceSource, ...] | None = None) -> list[str]:
    """Static provenance check: every source has a licence, https URL, 64-hex SHA-256, credit and a sane box."""
    problems = []
    seen = set()
    for s in sources or load_sources():
        if s.nasa_id in seen:
            problems.append(f"{s.nasa_id}: duplicate")
        seen.add(s.nasa_id)
        if not s.url.startswith("https://") or "nasa.gov" not in s.url:
            problems.append(f"{s.nasa_id}: url {s.url!r} is not an https NASA URL")
        if len(s.sha256) != 64 or any(c not in "0123456789abcdef" for c in s.sha256):
            problems.append(f"{s.nasa_id}: bad sha256")
        if "ublic domain" not in s.licence:
            problems.append(f"{s.nasa_id}: licence not public domain")
        if not s.credit or not s.date:
            problems.append(f"{s.nasa_id}: missing credit or date")
        x, y, w, h = s.box
        if not (
            0 <= x and 0 <= y and x + w <= s.size[0] + 1 and y + h <= s.size[1] + 1 and w > 20 and h > 20
        ):
            problems.append(f"{s.nasa_id}: face box outside the image")
    return problems


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_problems(cache_dir: Path) -> list[str]:
    problems = []
    for s in load_sources():
        p = cache_dir / s.filename
        if not p.exists():
            problems.append(f"{s.filename}: missing")
        elif _sha(p.read_bytes()) != s.sha256:
            problems.append(f"{s.filename}: sha256 mismatch")
    return problems


def write_attribution(cache_dir: Path) -> Path:
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Face source attribution",
        "",
        "All portraits are public-domain NASA images fetched from the NASA Image and Video Library.",
        "",
        f"Licence: {LICENCE}",
        "",
        "| file | person | role | credit | date | source | sha256 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in load_sources():
        lines.append(
            f"| {s.filename} | {s.person} | {s.role}/{s.use} | {s.credit} | {s.date} | {s.page_url} | {s.sha256} |"
        )
    path = cache_dir.parent / "ATTRIBUTION.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def fetch_sources(cache_dir: Path) -> list[str]:
    """Download missing portraits and verify SHA-256. Returns problems (empty = ready)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    problems = []
    for s in load_sources():
        p = cache_dir / s.filename
        if p.exists() and _sha(p.read_bytes()) == s.sha256:
            continue
        try:
            req = urllib.request.Request(s.url, headers={"User-Agent": "dataset-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310 - pinned https URL
                data = r.read()
        except OSError as exc:
            problems.append(f"{s.filename}: download failed ({exc})")
            continue
        if _sha(data) != s.sha256:
            problems.append(f"{s.filename}: downloaded file has the wrong sha256")
            continue
        p.write_bytes(data)
    if not problems:
        write_attribution(cache_dir)
    return problems


@lru_cache(maxsize=64)
def load_image(cache_dir: str, nasa_id: str) -> np.ndarray:
    im = cv2.imread(str(Path(cache_dir) / f"{nasa_id}.jpg"), cv2.IMREAD_COLOR)
    if im is None:
        raise FileNotFoundError(f"{nasa_id}.jpg not in {cache_dir}")
    return im[..., ::-1].copy()


# ---- tile rendering ------------------------------------------------------------------------------------------
def render_tile(
    cache_dir: str,
    src: FaceSource,
    size: tuple[int, int],
    face_frac: float,
    rng: random.Random,
    angle: float = 0.0,
    mirror: bool | None = None,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Crop/scale the portrait into a `size` tile with the face width = `face_frac` of the tile width.

    Returns (tile RGB, face quad in tile coordinates, clockwise from top-left).
    """
    img = load_image(cache_dir, src.nasa_id)
    x, y, w, h = src.box
    if mirror is None:
        mirror = rng.random() < 0.5
    if mirror:
        img = img[:, ::-1]
        x = src.size[0] - (x + w)
    tw, th = size
    s = face_frac * tw / w
    cx, cy = x + w / 2, y + h / 2
    # face centre inside the tile (fractions), jittered
    u = rng.uniform(0.38, 0.62) * tw
    v = rng.uniform(0.36, 0.52) * th
    a = np.deg2rad(angle)
    m = np.array(
        [[s * np.cos(a), s * np.sin(a), 0.0], [-s * np.sin(a), s * np.cos(a), 0.0]], dtype=np.float64
    )
    m[0, 2] = u - (m[0, 0] * cx + m[0, 1] * cy)
    m[1, 2] = v - (m[1, 0] * cx + m[1, 1] * cy)
    tile = cv2.warpAffine(
        np.ascontiguousarray(img),
        m,
        (tw, th),
        flags=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    corners = np.array([[x, y, 1], [x + w, y, 1], [x + w, y + h, 1], [x, y + h, 1]], dtype=np.float64)
    quad = [(float(px), float(py)) for px, py in (m @ corners.T).T]
    return tile, quad


def _label(canvas: np.ndarray, text: str, x: int, y: int, size: int = 22) -> None:
    from PIL import Image, ImageDraw

    im = Image.fromarray(canvas)
    d = ImageDraw.Draw(im)
    d.rectangle((x, y - size - 6, x + int(len(text) * size * 0.62) + 14, y + 4), fill=(0, 0, 0))
    d.text((x + 7, y), text, font=load_font("latin", size, 600), fill=(255, 255, 255), anchor="ls")
    canvas[:] = np.asarray(im)


def _layout(rng: random.Random, n: int, kind: str) -> list[tuple[int, int, int, int]]:
    """Tile rectangles (x, y, w, h) on the 1280x720 canvas."""
    W, H = CANVAS
    if kind == "grid":
        cols = 1 if n == 1 else 2 if n <= 4 else 3
        rows = (n + cols - 1) // cols
        gap = 12
        tw, th = (W - gap * (cols + 1)) // cols, (H - gap * (rows + 1)) // rows
        return [(gap + (i % cols) * (tw + gap), gap + (i // cols) * (th + gap), tw, th) for i in range(n)]
    # room: framed pictures scattered with no overlap
    rects: list[tuple[int, int, int, int]] = []
    for _ in range(400):
        if len(rects) == n:
            break
        tw = rng.randint(160, 360)
        th = int(tw * rng.uniform(0.9, 1.25))
        x, y = rng.randint(20, W - tw - 20), rng.randint(90, H - th - 20)
        if all(
            x + tw + 16 < rx or rx + rw + 16 < x or y + th + 16 < ry or ry + rh + 16 < y
            for rx, ry, rw, rh in rects
        ):
            rects.append((x, y, tw, th))
    return rects


def render_face_scene(
    rng: random.Random,
    cache_dir: str,
    people: list[FaceSource],
    kind: str | None = None,
    enrolled_ids: tuple[str, ...] = (),
) -> Sprite:
    """Scene with one tile per source in `people`. Face targets carry person_id and allowlisted flag."""
    kind = kind or rng.choice(("grid", "room", "grid"))
    n = len(people)
    rects = _layout(rng, n, kind)
    if len(rects) < n:  # room too crowded: fall back to a grid
        kind, rects = "grid", _layout(rng, n, "grid")
    rg = np.random.default_rng(rng.getrandbits(32))
    canvas = (
        backdrop(rg, "wall" if kind == "room" else "dark_room")
        if kind == "room"
        else np.full((CANVAS[1], CANVAS[0], 3), 22, np.uint8)
    )
    targets: list[SpriteTarget] = []
    for i, (src, (rx, ry, rw, rh)) in enumerate(zip(people, rects, strict=False)):
        frac = rng.uniform(0.2, 0.34) if kind == "grid" else rng.uniform(0.28, 0.42)
        tile, quad = render_tile(cache_dir, src, (rw, rh), frac, rng, angle=rng.uniform(-6, 6))
        if kind == "room":
            cv2.rectangle(canvas, (rx - 8, ry - 8), (rx + rw + 8, ry + rh + 8), (60, 40, 25), -1)
        canvas[ry : ry + rh, rx : rx + rw] = tile
        if kind == "grid":
            _label(canvas, f"Guest {i + 1}", rx + 8, ry + rh - 10)
        pts = [(rx + px, ry + py) for px, py in quad]
        targets.append(
            SpriteTarget(
                "face",
                pts,
                {
                    "person_id": src.person_id,
                    "source": src.nasa_id,
                    "allowlisted": src.person_id in enrolled_ids,
                    "tile": [rx, ry, rw, rh],
                },
            )
        )
    return Sprite(canvas, targets, context=[], meta={"scene": kind, "n_faces": n})


def render_face_negative(rng: random.Random, cache_dir: str) -> Sprite:
    """Scene with no face: torso crops below the chin, background corners and synthetic silhouettes."""
    from PIL import Image, ImageDraw

    n = rng.randint(2, 4)
    rects = _layout(rng, n, "grid")
    canvas = np.full((CANVAS[1], CANVAS[0], 3), 22, np.uint8)
    distractors: list[SpriteTarget] = []
    for rx, ry, rw, rh in rects:
        mode = rng.choice(("torso", "background", "silhouette"))
        if mode == "silhouette":
            im = Image.new("RGB", (rw, rh), (200, 205, 215))
            silhouette(ImageDraw.Draw(im), (0, 0, rw, rh))
            tile = np.asarray(im, dtype=np.uint8)
            kind = "silhouette"
        else:
            src = rng.choice(load_sources())
            img = load_image(cache_dir, src.nasa_id)
            x, y, w, h = src.box
            if mode == "torso":  # region well below the chin
                y0 = int(min(y + h * 1.3, img.shape[0] - 40))
                x0 = int(max(0, x + w / 2 - w * 1.4))
                crop = img[y0 : y0 + int(w * 1.2), x0 : x0 + int(w * 2.8)]
            else:  # background strip beside the head
                side_left = x > img.shape[1] - (x + w)
                bw = int(min(w * 1.2, (x if side_left else img.shape[1] - x - w) - 10))
                if bw < 60:
                    crop = img[: max(60, int(y * 0.7)), :]
                else:
                    x0 = 0 if side_left else int(x + w + 10)
                    crop = img[int(y * 0.2) : int(y + h * 0.9), x0 : x0 + bw]
            if crop.size == 0 or min(crop.shape[:2]) < 8:
                crop = np.full((60, 60, 3), 120, np.uint8)
            tile = cv2.resize(np.ascontiguousarray(crop), (rw, rh), interpolation=cv2.INTER_AREA)
            kind = mode
        canvas[ry : ry + rh, rx : rx + rw] = tile
        distractors.append(
            SpriteTarget("non_face_tile", rect_quad((rx, ry, rx + rw, ry + rh)), {"content": kind})
        )
    return Sprite(canvas, [], distractors=distractors, meta={"scene": "negative"})


def render_enroll_frame(
    rng: random.Random, cache_dir: str, src: FaceSource
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Webcam-style 1280x720 enrolment frame: the person large and centred, small pose/brightness jitter."""
    tile, quad = render_tile(cache_dir, src, CANVAS, rng.uniform(0.2, 0.34), rng, angle=rng.uniform(-8, 8))
    gain = rng.uniform(0.8, 1.1)
    tile = np.clip(tile.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    return tile, quad
