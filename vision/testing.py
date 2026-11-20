"""Test and acceptance support for the vision package: SPECIMEN scene generation and scoring.

Everything rendered here is synthetic and marked SPECIMEN (cards from ``eval.data_gen.video.cards``:
diagonal SPECIMEN watermark, Verhoeff-valid numbers starting with 0 or 1, PAN 4th character X, a
silhouette instead of a photo). No real ID, no real number, no personal data. ``eval`` is imported
lazily so importing ``vision`` never needs it.

``make_card_scene`` places a card on a desk image with tilt, distance, blur (whole or partial), glare,
brightness, noise and JPEG artefacts, and returns the ground-truth card quad and number quad.
``make_negative_scene`` builds frames that must NOT be masked as a card (empty desk, plain rectangle,
text screen, QR poster). ``score_scene`` turns a ``LaneResult`` into per-scene outcomes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from contracts import Category, Frame, LaneResult

CANVAS_W, CANVAS_H = 1280, 720
CARD_CATEGORIES = (Category.AADHAAR, Category.PAN)


@dataclass
class Scene:
    image: np.ndarray  # BGR uint8 720x1280x3
    kind: str  # "aadhaar" | "pan" | "desk" | "plain_rect" | "text_screen" | "qr_poster"
    card_quad: np.ndarray | None = None  # (4, 2) ground-truth card corners
    number_quad: np.ndarray | None = None
    number_text: str = ""  # the SPECIMEN number as drawn (for fake-OCR tests; never a real number)
    text_quads: list[np.ndarray] = field(default_factory=list)  # other text lines on the card (4, 2)
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def positive(self) -> bool:
        return self.card_quad is not None

    def frame(self, seq: int, pts: float) -> Frame:
        return Frame(seq=seq, pts=pts, arrival=0.0, image=self.image)


def _specimen_modules() -> tuple[Any, Any, Any]:
    from eval.data_gen.video import cards, dataset

    return cards, dataset, cards.CARD_W


def _backdrop(rng: random.Random, seed: int, name: str) -> np.ndarray:
    from eval.data_gen.video.dataset import backdrop

    img = backdrop(seed, name)
    return img


def _clutter(img: np.ndarray, rng: random.Random, avoid: np.ndarray | None) -> None:
    """A few distractor rectangles on the desk (kept off the card)."""
    for _ in range(rng.randint(1, 3)):
        w, h = rng.randint(60, 220), rng.randint(40, 140)
        x, y = rng.randint(0, CANVAS_W - w), rng.randint(0, CANVAS_H - h)
        if avoid is not None and cv2.pointPolygonTest(
            avoid.reshape(-1, 1, 2), (x + w / 2, y + h / 2), True
        ) > -max(w, h):
            continue
        color = tuple(int(c) for c in np.random.default_rng(rng.randint(0, 10**6)).integers(40, 200, 3))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, -1)


def _quad_ok(quad: np.ndarray, margin: int = 12) -> bool:
    from eval.sender.stamp import STAMP_EXCLUSION

    xs, ys = quad[:, 0], quad[:, 1]
    sx1, sy1, sx2, sy2 = STAMP_EXCLUSION
    inside = (
        xs.min() >= margin
        and ys.min() >= margin
        and xs.max() <= CANVAS_W - margin
        and ys.max() <= CANVAS_H - margin
    )
    stamp = not (xs.max() < sx1 or xs.min() > sx2 or ys.max() < sy1 or ys.min() > sy2)
    return bool(inside and not stamp)


def _place(
    sprite: np.ndarray,
    rng: random.Random,
    width_frac: float,
    tilt: float,
    center_x: tuple[float, float] = (0.28, 0.72),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(bgr scene without augmentation, card quad, sprite->scene 3x3 matrix)."""
    sh, sw = sprite.shape[:2]
    scale = width_frac * CANVAS_W / sw
    for _ in range(300):
        cx = rng.uniform(*center_x) * CANVAS_W
        cy = rng.uniform(0.35, 0.65) * CANVAS_H
        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), tilt, scale)
        m[0, 2] += cx - sw / 2
        m[1, 2] += cy - sh / 2
        corners = np.array([[0, 0, 1], [sw, 0, 1], [sw, sh, 1], [0, sh, 1]], np.float64)
        quad = (m @ corners.T).T.astype(np.float32)
        if _quad_ok(quad):
            full = np.vstack([m, [0, 0, 1]])
            return sprite, quad, full
    raise RuntimeError("could not place the card inside the frame")


# Text lines of the SPECIMEN renderers in sprite coordinates (approximate; used by fake-OCR tests only)
_TEXT_LAYOUT: dict[str, list[tuple[int, int, int, int]]] = {
    "aadhaar": [
        (16, 8, 440, 46),
        (184, 80, 380, 104),
        (184, 112, 340, 136),
        (184, 144, 430, 168),
        (300, 350, 600, 380),
    ],
    "pan": [
        (16, 8, 580, 46),
        (200, 200, 390, 224),
        (200, 232, 390, 256),
        (200, 264, 370, 288),
        (24, 364, 430, 388),
    ],
}


def _rect_quad(full: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = rect
    pts = np.array([[x1, y1, 1], [x2, y1, 1], [x2, y2, 1], [x1, y2, 1]], np.float64)
    return (full @ pts.T).T[:, :2].astype(np.float32)


def scene_ocr(scene: Scene, *, number_text: str | None = None, conf: float = 0.98, with_text: bool = True):
    """A fake ``OcrProvider`` for ``scene``: the number box with ``number_text`` (default: the true
    SPECIMEN number) plus the other text lines. For logic tests that must not load the OCR models."""
    from contracts import OcrResult, Quad

    assert scene.number_quad is not None

    def q(a: np.ndarray) -> Quad:
        return Quad(tuple((float(x), float(y)) for x, y in a))  # type: ignore[arg-type]

    texts = [scene.number_text if number_text is None else number_text]
    boxes = [q(scene.number_quad)]
    if with_text:
        for tq in scene.text_quads:
            boxes.append(q(tq))
            texts.append("SPECIMEN LINE")
    n = len(boxes)
    return lambda frame: OcrResult(tuple(boxes), tuple(texts), (conf,) * n, ("latin",) * n)


def make_card_scene(
    kind: str,
    index: int,
    seed: int = 1,
    *,
    tilt: float | None = None,
    width_frac: float | None = None,
    blur: float = 0.0,
    partial_blur: float = 0.0,
    glare: bool = False,
    brightness: float = 1.0,
    noise: float = 3.0,
    jpeg: int | None = None,
    clutter: bool = True,
    occlude_number: bool = False,
    center_x: tuple[float, float] = (0.28, 0.72),
) -> Scene:
    """One SPECIMEN card on a desk. ``kind`` is ``aadhaar`` or ``pan``. Everything is seeded."""
    cards, dataset, card_w = _specimen_modules()
    from eval.data_gen.common.seeds import py_rng

    rng = py_rng(seed, "scene", kind, index)
    render = cards.render_aadhaar if kind == "aadhaar" else cards.render_pan
    card = render(py_rng(seed, "card", kind, index))
    tilt = rng.uniform(-15.0, 15.0) if tilt is None else tilt
    width_frac = rng.uniform(0.28, 0.5) if width_frac is None else width_frac
    sprite = cv2.cvtColor(card.image, cv2.COLOR_RGB2BGR)
    if occlude_number:  # a SPECIMEN sticker over the number: the card stays visible, the digits do not
        x1, y1, x2, y2 = card.number_bbox
        cv2.rectangle(sprite, (x1, y1), (x2, y2), (60, 60, 60), -1)
    sprite, quad, full = _place(sprite, rng, width_frac, tilt, center_x)
    sh, sw = sprite.shape[:2]
    backdrop = cv2.cvtColor(dataset.backdrop(seed, f"{kind}{index}"), cv2.COLOR_RGB2BGR)
    if clutter:
        _clutter(backdrop, rng, quad)
    warped = cv2.warpAffine(
        sprite,
        full[:2],
        (CANVAS_W, CANVAS_H),
        flags=cv2.INTER_AREA if width_frac * CANVAS_W < sw else cv2.INTER_LINEAR,
    )
    mask = cv2.warpAffine(np.full((sh, sw), 255, np.uint8), full[:2], (CANVAS_W, CANVAS_H))
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    img = (backdrop.astype(np.float32) * (1 - alpha) + warped.astype(np.float32) * alpha).astype(np.uint8)
    x1, y1, x2, y2 = card.number_bbox
    nq = np.array([[x1, y1, 1], [x2, y1, 1], [x2, y2, 1], [x1, y2, 1]], np.float64)
    number_quad = (full @ nq.T).T[:, :2].astype(np.float32)
    layout = _TEXT_LAYOUT[kind]
    text_quads = [_rect_quad(full, r) for r in layout]

    card_mask = mask > 127
    if partial_blur > 0:  # blur one half of the card (the half that holds the number when it is odd)
        region = np.zeros((CANVAS_H, CANVAS_W), np.uint8)
        cx = float(quad[:, 0].mean())
        if index % 2:
            region[:, int(cx) :] = 255
        else:
            region[:, : int(cx)] = 255
        region &= mask
        blurred = cv2.GaussianBlur(img, (0, 0), partial_blur)
        img = np.where(region[..., None] > 0, blurred, img)
    if blur > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    if glare:  # a soft bright blob on the card, offset from the number so it stays partly readable
        cx, cy = (
            float(quad[:, 0].mean()) + rng.uniform(-90, 90),
            float(quad[:, 1].mean()) + rng.uniform(-60, 60),
        )
        yy, xx = np.mgrid[0:CANVAS_H, 0:CANVAS_W]
        blob = np.exp(-(((xx - cx) / 70.0) ** 2 + ((yy - cy) / 45.0) ** 2))
        img = np.clip(img.astype(np.float32) + 110.0 * blob[..., None] * card_mask[..., None], 0, 255).astype(
            np.uint8
        )
    img = np.clip(img.astype(np.float32) * brightness, 0, 255)
    if noise > 0:
        img = img + np.random.default_rng(seed * 7919 + index).normal(0, noise, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    if jpeg is not None:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg)])
        assert ok
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    params = {
        "tilt": round(tilt, 1),
        "width_frac": round(width_frac, 2),
        "blur": blur,
        "partial_blur": partial_blur,
        "glare": glare,
        "brightness": brightness,
        "jpeg": jpeg,
        "occlude_number": occlude_number,
    }
    return Scene(img, kind, quad, number_quad, card.number, text_quads, params)


def make_negative_scene(kind: str, index: int, seed: int = 1) -> Scene:
    """A frame with no card that a card detector must leave alone."""
    from eval.data_gen.common.seeds import py_rng

    rng = py_rng(seed, "neg", kind, index)
    _, dataset, _ = _specimen_modules()
    img = cv2.cvtColor(dataset.backdrop(seed, f"neg{kind}{index}"), cv2.COLOR_RGB2BGR)
    if kind == "plain_rect":  # ID-1 shaped plain sheet: card-like shape, no text
        w = int(rng.uniform(0.3, 0.45) * CANVAS_W)
        h = int(w / 1.585)
        x, y = rng.randint(80, CANVAS_W - w - 80), rng.randint(100, CANVAS_H - h - 100)
        cv2.rectangle(img, (x, y), (x + w, y + h), (240, 240, 235), -1)
    elif kind == "text_screen":  # a 16:10 panel of unrelated text (no card number, no PAN)
        w, h = 760, 475
        x, y = rng.randint(60, CANVAS_W - w - 60), rng.randint(40, CANVAS_H - h - 60)
        cv2.rectangle(img, (x, y), (x + w, y + h), (30, 30, 36), -1)
        words = ["Match", "Score", "Level", "Wins", "Team", "Squad", "Zone", "Loot", "Round", "Ready"]
        for row in range(11):
            line = " ".join(rng.choice(words) for _ in range(4)) + f" {rng.randint(1, 99)}"
            cv2.putText(
                img,
                line,
                (x + 24, y + 40 + row * 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (235, 235, 235),
                2,
                cv2.LINE_AA,
            )
    elif kind == "qr_poster":
        from eval.data_gen.video.qr import render_qr_poster

        poster = cv2.cvtColor(render_qr_poster(rng).image, cv2.COLOR_RGB2BGR)
        scale = rng.uniform(0.7, 0.95)
        poster = cv2.resize(poster, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ph, pw = poster.shape[:2]
        x, y = rng.randint(100, CANVAS_W - pw - 100), rng.randint(0, max(1, CANVAS_H - ph))
        img[y : y + ph, x : x + pw] = poster
    elif kind != "desk":
        raise ValueError(f"unknown negative scene {kind!r}")
    return Scene(img, kind)


def augmented_set(per_kind: int = 36, negatives_per_kind: int = 8, seed: int = 1) -> list[Scene]:
    """``2 * per_kind`` positives with mixed augmentation plus negatives of four kinds.

    Conditions cycle through: clean, tilt (up to 15 degrees), far (small card), whole-image blur,
    partial blur, glare, dim, bright, JPEG. Distance and tilt are always randomised.
    """
    recipes: list[dict[str, Any]] = [
        {},
        {"tilt": None},
        {"width_frac": 0.24},
        {"blur": 1.3},
        {"partial_blur": 2.2},
        {"glare": True},
        {"brightness": 0.7},
        {"brightness": 1.2, "noise": 5.0},
        {"jpeg": 55},
    ]
    scenes: list[Scene] = []
    for kind in ("aadhaar", "pan"):
        for i in range(per_kind):
            scenes.append(make_card_scene(kind, i, seed, **recipes[i % len(recipes)]))
    for neg in ("desk", "plain_rect", "text_screen", "qr_poster"):
        for i in range(negatives_per_kind):
            scenes.append(make_negative_scene(neg, i, seed))
    return scenes


# ---------------------------------------------------------------------------- scoring
def _poly_clip_area(a: np.ndarray, b: np.ndarray) -> float:
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    return float(inter)


def _area(p: np.ndarray) -> float:
    return float(abs(cv2.contourArea(p.astype(np.float32))))


@dataclass
class SceneScore:
    positive: bool
    masked: bool  # some card-category request overlaps the card (IoU >= 0.5), or any request on a negative
    covered: bool  # the union of requests covers >= 90 % of the card area
    coverage: float
    requests: int


def score_scene(scene: Scene, result: LaneResult) -> SceneScore:
    reqs = [r for r in result.requests if r.category in CARD_CATEGORIES]
    if not scene.positive:
        return SceneScore(False, bool(reqs), False, 0.0, len(reqs))
    assert scene.card_quad is not None
    card = scene.card_quad
    card_area = _area(card)
    best_iou, mask_img = 0.0, np.zeros((CANVAS_H, CANVAS_W), np.uint8)
    for r in reqs:
        for q in r.quads:
            pts = np.array(q.pts, np.float32)
            inter = _poly_clip_area(card, pts)
            best_iou = max(best_iou, inter / max(1e-6, card_area + _area(pts) - inter))
            cv2.fillConvexPoly(mask_img, pts.astype(np.int32), 255)
    card_mask = np.zeros_like(mask_img)
    cv2.fillConvexPoly(card_mask, card.astype(np.int32), 255)
    coverage = float(((mask_img > 0) & (card_mask > 0)).sum() / max(1, (card_mask > 0).sum()))
    return SceneScore(True, best_iou >= 0.5, coverage >= 0.9, coverage, len(reqs))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return math.nan
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q / 100.0 * (len(s) - 1))))]
