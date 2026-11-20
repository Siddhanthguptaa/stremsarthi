"""Game-screen templates: a UI anchor found by multi-scale ``matchTemplate``, then fixed regions (spec 5.10).

A template is an anchor image (a static piece of the game's UI, grayscale) plus regions given in reference
coordinates (a 1280x720 frame): where the anchor sits and where the player ID, room ID and password are, relative
to it. When the anchor is found at scale ``k`` and position ``p`` the regions are placed at ``p + k * region``.
This masks the value fields without reading them, so it also works when the OCR fails on the digits.

Templates live in ``templates/<name>.png`` + ``templates/<name>.json`` next to this file::

    {"game": "bgmi", "anchor": [x, y, w, h], "threshold": 0.62,
     "regions": [{"category": "room_credential", "rect": [x, y, w, h]}, ...]}

The shipped templates are derived from our own SPECIMEN overlays (``eval/``): those are NOT the real games.
Real BGMI / Free Fire layouts change with the game version and the phone (UNVERIFIED); calibrate with
screenshots from the streamer's phone (``vision.testing_screens.calibrate`` shows how to build a template from a
reference screenshot) before relying on this route against the real games. The OCR label route
(``games.py``) does not depend on fixed positions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from contracts import Category

log = logging.getLogger("vision.screens")

DEFAULT_DIR = Path(__file__).parent / "templates"
REF_WIDTH = 1280
WORK_WIDTH = 320  # the match runs on a frame downscaled to this width
SCALES = (0.5, 0.6, 0.7, 0.85, 1.0, 1.2, 1.4)  # frame content scale relative to the reference layout
DEFAULT_THRESHOLD = 0.8
REGION_CATEGORIES = {Category.GAME_ID, Category.ROOM_CREDENTIAL}


@dataclass(frozen=True, slots=True)
class Region:
    category: Category
    rect: tuple[float, float, float, float]  # x, y, w, h in reference coordinates


@dataclass(slots=True)
class GameTemplate:
    name: str
    game: str
    anchor: np.ndarray  # grayscale uint8, at reference scale
    anchor_xywh: tuple[float, float, float, float]
    regions: tuple[Region, ...]
    threshold: float = DEFAULT_THRESHOLD
    # a weak anchor (a generic shape such as a profile card) counts only with other evidence of the game screen:
    # a strong anchor of the same frame, or game cue words in the OCR text
    weak: bool = False

    def __post_init__(self) -> None:
        if self.anchor.ndim != 2 or self.anchor.size == 0:
            raise ValueError(f"template {self.name}: the anchor must be a non-empty grayscale image")
        if not self.regions:
            raise ValueError(f"template {self.name}: no regions")
        for r in self.regions:
            if r.category not in REGION_CATEGORIES:
                raise ValueError(f"template {self.name}: region category {r.category} is not a game category")


@dataclass(frozen=True, slots=True)
class TemplateHit:
    template: str
    game: str
    score: float
    scale: float
    weak: bool
    regions: tuple[tuple[Category, np.ndarray], ...]  # (category, quad 4x2 in frame pixels)


def load_templates(directory: Path | str | None = None) -> list[GameTemplate]:
    """Every ``<name>.json`` + ``<name>.png`` pair in ``directory`` (default: the shipped templates)."""
    root = Path(directory) if directory is not None else DEFAULT_DIR
    out: list[GameTemplate] = []
    if not root.is_dir():
        return out
    for meta_path in sorted(root.glob("*.json")):
        png = meta_path.with_suffix(".png")
        if not png.is_file():
            log.warning("template %s has no image", meta_path.stem)
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        anchor = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        if anchor is None:
            log.warning("template %s: unreadable image", meta_path.stem)
            continue
        out.append(
            GameTemplate(
                name=meta_path.stem,
                game=str(meta.get("game", meta_path.stem)),
                anchor=anchor,
                anchor_xywh=tuple(float(v) for v in meta["anchor"]),  # type: ignore[arg-type]
                regions=tuple(
                    Region(Category(r["category"]), tuple(float(v) for v in r["rect"]))  # type: ignore[arg-type]
                    for r in meta["regions"]
                ),
                threshold=float(meta.get("threshold", DEFAULT_THRESHOLD)),
                weak=bool(meta.get("weak", False)),
            )
        )
    return out


def save_template(directory: Path | str, tpl: GameTemplate) -> None:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(root / f"{tpl.name}.png"), tpl.anchor)
    meta = {
        "game": tpl.game,
        "anchor": list(tpl.anchor_xywh),
        "threshold": tpl.threshold,
        "weak": tpl.weak,
        "regions": [{"category": r.category.value, "rect": list(r.rect)} for r in tpl.regions],
    }
    (root / f"{tpl.name}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


class TemplateMatcher:
    """Finds the anchors of a template set in a frame and places their regions."""

    def __init__(self, templates: list[GameTemplate]) -> None:
        self.templates = templates
        # one resized copy of every anchor per scale, built once: the frame work width is fixed
        self._cache: dict[tuple[str, float, float], np.ndarray] = {}

    def _scaled(self, tpl: GameTemplate, k: float, ref_to_work: float) -> np.ndarray | None:
        key = (tpl.name, k, round(ref_to_work, 4))
        if key not in self._cache:
            f = k * ref_to_work
            w, h = max(1, round(tpl.anchor.shape[1] * f)), max(1, round(tpl.anchor.shape[0] * f))
            if w < 8 or h < 8:
                return None
            self._cache[key] = cv2.resize(tpl.anchor, (w, h), interpolation=cv2.INTER_AREA)
        return self._cache[key]

    def match(self, image: np.ndarray) -> list[TemplateHit]:
        if not self.templates:
            return []
        ih, iw = image.shape[:2]
        s = min(1.0, WORK_WIDTH / float(iw))
        small = (
            cv2.resize(image, (round(iw * s), round(ih * s)), interpolation=cv2.INTER_AREA)
            if s < 1
            else image
        )
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        # frame pixel -> reference pixel factor at k=1: the frame is assumed to be a REF_WIDTH-wide layout
        ref_to_work = (iw * s) / REF_WIDTH
        hits: list[TemplateHit] = []
        for tpl in self.templates:
            best: tuple[float, float, tuple[int, int]] | None = None
            for k in SCALES:
                t = self._scaled(tpl, k, ref_to_work)
                if t is None or t.shape[0] >= gray.shape[0] or t.shape[1] >= gray.shape[1]:
                    continue
                if float(t.std()) < 3.0:
                    continue  # a flat anchor matches anything
                res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(res)
                if best is None or score > best[0]:
                    best = (float(score), k, loc)
            if best is None or best[0] < tpl.threshold:
                continue
            score, k, loc = best
            f = k * ref_to_work / s  # reference pixel -> frame pixel
            ax, ay, _, _ = tpl.anchor_xywh
            ox, oy = loc[0] / s - ax * f, loc[1] / s - ay * f
            quads = []
            for r in tpl.regions:
                x, y, w, h = r.rect
                x1, y1, x2, y2 = ox + x * f, oy + y * f, ox + (x + w) * f, oy + (y + h) * f
                x1, y1 = max(0.0, x1), max(0.0, y1)
                x2, y2 = min(float(iw), x2), min(float(ih), y2)
                if x2 - x1 < 4 or y2 - y1 < 4:
                    continue
                quads.append((r.category, np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float32)))
            if quads:
                hits.append(TemplateHit(tpl.name, tpl.game, score, k, tpl.weak, tuple(quads)))
        return hits
