"""Test and calibration helpers for the ``screens`` detector (T2.9): template calibration from a reference
screenshot, the shipped SPECIMEN templates, fake OCR results and scene scoring.

Everything is SPECIMEN: the overlays come from ``eval.data_gen.video.games`` / ``screens`` (our own UIs with
invented IDs, never the real games and never real notifications). ``eval`` is imported lazily.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from contracts import Category, OcrResult, Quad
from vision.detectors.screens.templates import DEFAULT_DIR, GameTemplate, Region, save_template

Box = tuple[str, tuple[int, int, int, int]]

# Layout of the SPECIMEN overlays (eval/data_gen/video/games.py), 1280x720: where the anchors sit and where the
# value fields are. The profile card is the same for both styles; the room panel differs.
PROFILE_ANCHOR = (20, 16, 92, 94)  # card edge and avatar (static, the name and ID beside it vary)
PROFILE_REGIONS = ((Category.GAME_ID, (108, 62, 312, 42)),)
ROOM_PANELS = {
    # the "CUSTOM ROOM" header bar of the room panel with a margin: both ends of the bar pin the anchor
    # (a bare flat bar of one game also "matches" the other game's bar at any shift)
    "bgmi": (748, 122, 514, 80),
    "freefire": (28, 142, 454, 80),
}
ROOM_FIELDS = {
    "bgmi": ((775, 255, 455, 60), (775, 372, 455, 66)),  # room ID, password
    "freefire": ((65, 268, 400, 64), (65, 392, 400, 66)),
}


def calibrate(
    reference_bgr: np.ndarray,
    name: str,
    game: str,
    anchor_xywh: tuple[float, float, float, float],
    regions: tuple[tuple[Category, tuple[float, float, float, float]], ...],
    threshold: float = 0.8,
    weak: bool = False,
) -> GameTemplate:
    """Build a template from a reference screenshot (a 1280x720 capture of the game's lobby): the anchor is the
    crop of ``anchor_xywh`` (pick static UI, never IDs or names), ``regions`` are the fields to mask."""
    h, w = reference_bgr.shape[:2]
    if (w, h) != (1280, 720):
        reference_bgr = cv2.resize(reference_bgr, (1280, 720), interpolation=cv2.INTER_AREA)
    x, y, aw, ah = (int(v) for v in anchor_xywh)
    gray = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY)
    return GameTemplate(
        name=name,
        game=game,
        anchor=gray[y : y + ah, x : x + aw].copy(),
        anchor_xywh=(float(x), float(y), float(aw), float(ah)),
        regions=tuple(Region(c, r) for c, r in regions),
        threshold=threshold,
        weak=weak,
    )


def specimen_lobby(style: str, seed: int = 1) -> np.ndarray:
    """A SPECIMEN room lobby (BGR 1280x720) of ``style`` (``bgmi`` | ``freefire``)."""
    from eval.data_gen.video.games import render_lobby

    return cv2.cvtColor(render_lobby(random.Random(seed), style).image, cv2.COLOR_RGB2BGR)


def specimen_templates(seed: int = 1) -> list[GameTemplate]:
    out: list[GameTemplate] = []
    for style in ("bgmi", "freefire"):
        ref = specimen_lobby(style, seed)
        if style == "bgmi":  # the profile card is the same in both SPECIMEN styles (one grayscale anchor)
            out.append(calibrate(ref, "profile", "specimen", PROFILE_ANCHOR, PROFILE_REGIONS, weak=True))
        room_id, password = ROOM_FIELDS[style]
        out.append(
            calibrate(
                ref,
                f"{style}_room",
                style,
                ROOM_PANELS[style],
                ((Category.ROOM_CREDENTIAL, room_id), (Category.ROOM_CREDENTIAL, password)),
            )
        )
    return out


def write_default_templates(directory: Path | str = DEFAULT_DIR) -> list[str]:
    """(Re)write the shipped SPECIMEN templates; returns their names. Run: ``python -m vision.testing_screens``."""
    tpls = specimen_templates()
    for t in tpls:
        save_template(directory, t)
    return [t.name for t in tpls]


def fake_ocr(boxes: list[Box], conf: float = 0.97):
    """An OCR provider returning ``boxes`` (text, xyxy) for any frame."""
    result = OcrResult(
        tuple(Quad.from_xyxy(*r) for _, r in boxes),
        tuple(t for t, _ in boxes),
        (conf,) * len(boxes),
        ("latin",) * len(boxes),
    )
    return lambda frame: result


def coverage_of(truth_quad: Any, quads: list[np.ndarray]) -> float:
    """Share of the truth quad's area covered by the union of ``quads`` (rasterised)."""
    t = np.array(truth_quad, np.float32)
    x1, y1 = np.floor(t.min(axis=0)).astype(int) - 2
    x2, y2 = np.ceil(t.max(axis=0)).astype(int) + 2
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(1280, x2), min(720, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    tm = np.zeros((y2 - y1, x2 - x1), np.uint8)
    cv2.fillPoly(tm, [np.round(t - [x1, y1]).astype(np.int32)], 1)
    dm = np.zeros_like(tm)
    for q in quads:
        cv2.fillPoly(dm, [np.round(q - [x1, y1]).astype(np.int32)], 1)
    total = int(tm.sum())
    return float((tm & dm).sum()) / total if total else 0.0


if __name__ == "__main__":
    print(write_default_templates())
