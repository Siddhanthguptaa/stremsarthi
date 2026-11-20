"""Scene scripts for the stamp sender: card_static, card_moving, card_hold, card_absent, many_targets, idle.

Pure numpy + Pillow + OpenCV. Targets are SPECIMEN cards/QR posters placed away from the stamp region
(STAMP_EXCLUSION plus a margin); a test asserts this for every scene at every frame.
Per-frame ground truth (target boxes, and corners for rotated cards) is returned alongside the pixels, and
``ScenePlayer.targets(index)`` returns it without drawing (the tap reader scores output frames with it).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import cv2
import numpy as np

from ..data_gen.video.cards import render_aadhaar, render_pan
from ..data_gen.video.dataset import backdrop
from ..data_gen.video.qr import render_qr_poster
from .stamp import CANVAS_H, CANVAS_W, STAMP_EXCLUSION

STAMP_MARGIN = 16
MARGIN = 24
SCENE_SEED = 1234
SCENE_NAMES = ("card_static", "card_moving", "card_hold", "card_absent", "many_targets", "idle")
DEFAULT_DURATION_S = 40.0


@dataclass(frozen=True)
class Target:
    category: str
    quad: tuple[int, int, int, int]  # x1, y1, x2, y2: the axis-aligned bounding box
    #: the four corners (tl, tr, br, bl) when the target is rotated or in perspective; None for upright ones
    corners: tuple[tuple[float, float], ...] | None = None
    #: name of the pre-rendered sprite this target was drawn from (see ``sprite_array``/``sprite_number``)
    ref: str | None = None

    def polygon(self) -> tuple[tuple[float, float], ...]:
        if self.corners is not None:
            return self.corners
        x1, y1, x2, y2 = self.quad
        return ((x1, y1), (x2, y1), (x2, y2), (x1, y2))


def _resize(img: np.ndarray, width: int) -> np.ndarray:
    h, w = img.shape[:2]
    return cv2.resize(img, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)


def _scale_box(box: tuple[int, int, int, int], k: float) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    return (int(x1 * k), int(y1 * k), int(round(x2 * k)), int(round(y2 * k)))


class _Sprites:
    """Pre-rendered sprites (built once per process, seeded)."""

    def __init__(self, seed: int = SCENE_SEED) -> None:
        big = render_aadhaar(random.Random(seed))
        small = render_aadhaar(random.Random(seed + 3))
        self.aadhaar = _resize(big.image, 440)
        self.pan = _resize(render_pan(random.Random(seed + 1)).image, 440)
        self.qr = _resize(render_qr_poster(random.Random(seed + 2)).image, 240)
        self.aadhaar_small = _resize(small.image, 300)
        # SPECIMEN numbers (synthetic, leading digit 0 or 1) so the card checker can score exact reads
        self.numbers = {"aadhaar": big.number, "aadhaar_small": small.number}
        # the opaque box that holds the number, in sprite pixels (x1, y1, x2, y2)
        self.number_boxes = {
            "aadhaar": _scale_box(big.number_bbox, 440 / 640),
            "aadhaar_small": _scale_box(small.number_bbox, 300 / 640),
        }


_SPRITES: _Sprites | None = None


def sprites() -> _Sprites:
    global _SPRITES
    if _SPRITES is None:
        _SPRITES = _Sprites()
    return _SPRITES


def sprite_array(ref: str) -> np.ndarray:
    """The unmasked reference sprite (RGB) a target was drawn from."""
    return getattr(sprites(), ref)  # type: ignore[no-any-return]


def sprite_number(ref: str) -> str | None:
    """The SPECIMEN Aadhaar number printed on that sprite (None when it is not an Aadhaar card)."""
    return sprites().numbers.get(ref)


def sprite_number_box(ref: str) -> tuple[int, int, int, int] | None:
    """Where the number sits on that sprite, in sprite pixels."""
    return sprites().number_boxes.get(ref)


def _blit(canvas: np.ndarray, sprite: np.ndarray, x: int, y: int) -> tuple[int, int, int, int]:
    h, w = sprite.shape[:2]
    canvas[y : y + h, x : x + w] = sprite
    return (x, y, x + w, y + h)


def _warp_blit(canvas: np.ndarray, sprite: np.ndarray, dst: np.ndarray) -> None:
    """Draw `sprite` into `canvas` so its corners land on `dst` (4x2 float, tl tr br bl)."""
    h, w = sprite.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
    size = (canvas.shape[1], canvas.shape[0])
    warped = cv2.warpPerspective(sprite, m, size, flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, size, flags=cv2.INTER_LINEAR)
    sel = mask > 127
    canvas[sel] = warped[sel]


def _clutter(canvas: np.ndarray) -> None:
    """Non-card clutter so 'absent' scenes are not blank: a grey notebook and a mug."""
    cv2.rectangle(canvas, (860, 380), (1180, 660), (70, 90, 70), -1)
    cv2.rectangle(canvas, (880, 400), (1160, 420), (200, 200, 190), -1)
    cv2.circle(canvas, (200, 560), 70, (180, 60, 50), -1)


class Scene:
    name = "scene"

    def __init__(self, fps: int = 30) -> None:
        self.fps = fps
        self.duration_s = DEFAULT_DURATION_S  # ScenePlayer sets the scripted length
        self.base = backdrop(SCENE_SEED, f"scene_{self.name}", (CANVAS_W, CANVAS_H))
        self.setup()

    def setup(self) -> None:  # pragma: no cover - overridden
        pass

    def render(self, t: float) -> tuple[np.ndarray, list[Target]]:
        """RGB uint8 canvas (a fresh copy) and the target boxes visible at scene time `t` seconds."""
        raise NotImplementedError

    def targets_at(self, t: float) -> list[Target]:
        """Ground truth at scene time `t` without drawing pixels (overridden where it is cheap)."""
        return self.render(t)[1]


class CardStatic(Scene):
    name = "card_static"

    def setup(self) -> None:
        s = sprites()
        self.pos = (420, 200)
        self.frame = self.base.copy()
        self.target = Target("aadhaar", _blit(self.frame, s.aadhaar, *self.pos), ref="aadhaar")

    def render(self, t: float):
        return self.frame.copy(), [self.target]

    def targets_at(self, t: float) -> list[Target]:
        return [self.target]


class CardMoving(Scene):
    name = "card_moving"

    def setup(self) -> None:
        self.s = sprites().aadhaar
        self.h, self.w = self.s.shape[:2]
        self.x_range = (MARGIN, CANVAS_W - self.w - MARGIN)
        self.y_range = (STAMP_EXCLUSION[3] + STAMP_MARGIN + 8, CANVAS_H - self.h - MARGIN)

    def _xy(self, t: float) -> tuple[int, int]:
        # Lissajous path, ~0.4 Hz horizontally: a hand slowly moving the card
        fx = 0.5 + 0.5 * math.sin(2 * math.pi * 0.20 * t)
        fy = 0.5 + 0.5 * math.sin(2 * math.pi * 0.13 * t + 1.0)
        x = int(self.x_range[0] + fx * (self.x_range[1] - self.x_range[0]))
        y = int(self.y_range[0] + fy * (self.y_range[1] - self.y_range[0]))
        return x, y

    def render(self, t: float):
        x, y = self._xy(t)
        frame = self.base.copy()
        return frame, [Target("aadhaar", _blit(frame, self.s, x, y), ref="aadhaar")]

    def targets_at(self, t: float) -> list[Target]:
        x, y = self._xy(t)
        return [Target("aadhaar", (x, y, x + self.w, y + self.h), ref="aadhaar")]


class CardHold(Scene):
    """A card held in view: still (0-30 %), slowly moving (30-65 %), then partially tilted (65-100 %).

    The tilt phase rotates the card up to 25 degrees and adds a mild perspective squeeze, so its target has
    four corners that are not an upright rectangle. It stays well clear of the stamp.
    """

    name = "card_hold"

    def setup(self) -> None:
        self.s = sprites().aadhaar
        self.h, self.w = self.s.shape[:2]
        self.pos = (420, 200)
        self.centre = (self.pos[0] + self.w / 2.0, self.pos[1] + self.h / 2.0)

    def _phase(self, t: float) -> tuple[str, float]:
        total = max(self.duration_s, 1.0)
        f = (t % total) / total
        if f < 0.30:
            return "static", 0.0
        if f < 0.65:
            return "moving", t - 0.30 * total
        return "tilt", t - 0.65 * total

    def _corners(self, t: float) -> np.ndarray:
        phase, u = self._phase(t)
        cx, cy = self.centre
        theta = 0.0
        squeeze = 0.0
        if phase == "moving":
            cx += 150.0 * math.sin(2 * math.pi * 0.06 * u)
            cy += 60.0 * math.sin(2 * math.pi * 0.045 * u + 1.0)
        elif phase == "tilt":
            theta = math.radians(25.0) * math.sin(2 * math.pi * 0.09 * u)
            squeeze = 0.10 * math.sin(2 * math.pi * 0.07 * u + 0.6)
        hw, hh = self.w / 2.0, self.h / 2.0
        pts = []
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x = sx * hw * (1.0 - squeeze * (-sy))  # one horizontal edge shrinks, the other grows
            y = sy * hh
            pts.append(
                (
                    cx + x * math.cos(theta) - y * math.sin(theta),
                    cy + x * math.sin(theta) + y * math.cos(theta),
                )
            )
        return np.array(pts, dtype=np.float32)

    def _target(self, corners: np.ndarray) -> Target:
        xs, ys = corners[:, 0], corners[:, 1]
        box = (
            int(math.floor(xs.min())),
            int(math.floor(ys.min())),
            int(math.ceil(xs.max())),
            int(math.ceil(ys.max())),
        )
        poly = tuple((float(x), float(y)) for x, y in corners)
        return Target("aadhaar", box, corners=poly, ref="aadhaar")

    def render(self, t: float):
        corners = self._corners(t)
        frame = self.base.copy()
        _warp_blit(frame, self.s, corners)
        return frame, [self._target(corners)]

    def targets_at(self, t: float) -> list[Target]:
        return [self._target(self._corners(t))]


class CardAbsent(Scene):
    name = "card_absent"

    def setup(self) -> None:
        self.frame = self.base.copy()
        _clutter(self.frame)

    def render(self, t: float):
        return self.frame.copy(), []

    def targets_at(self, t: float) -> list[Target]:
        return []


class ManyTargets(Scene):
    name = "many_targets"

    def setup(self) -> None:
        s = sprites()
        self.frame = self.base.copy()
        y_top = STAMP_EXCLUSION[3] + STAMP_MARGIN + 8
        self.targets = [
            Target("aadhaar", _blit(self.frame, s.aadhaar_small, 40, y_top), ref="aadhaar_small"),
            Target("pan", _blit(self.frame, _resize(s.pan, 300), 400, y_top)),
            Target("upi_qr", _blit(self.frame, s.qr, 800, y_top)),
            Target("aadhaar", _blit(self.frame, s.aadhaar_small, 80, 420), ref="aadhaar_small"),
            Target("pan", _blit(self.frame, _resize(s.pan, 300), 460, 440)),
        ]

    def render(self, t: float):
        return self.frame.copy(), list(self.targets)

    def targets_at(self, t: float) -> list[Target]:
        return list(self.targets)


class Idle(Scene):
    name = "idle"

    def setup(self) -> None:
        self.frame = self.base.copy()

    def render(self, t: float):
        frame = self.frame.copy()
        # a small moving dot so the picture is not perfectly static (no target)
        x = int(640 + 300 * math.sin(2 * math.pi * 0.1 * t))
        cv2.circle(frame, (x, 400), 24, (230, 230, 230), -1)
        return frame, []

    def targets_at(self, t: float) -> list[Target]:
        return []


_SCENES: dict[str, type[Scene]] = {
    c.name: c for c in (CardStatic, CardMoving, CardHold, CardAbsent, ManyTargets, Idle)
}


def parse_script(script: str) -> list[tuple[str, float]]:
    """``"card_absent:10,card_static:40"`` -> [("card_absent", 10.0), ("card_static", 40.0)].

    A bare scene name uses the default duration (40 s).
    """
    out: list[tuple[str, float]] = []
    for part in (p.strip() for p in script.split(",") if p.strip()):
        name, _, dur = part.partition(":")
        if name not in _SCENES:
            raise ValueError(f"unknown scene {name!r}; choose from {', '.join(SCENE_NAMES)}")
        out.append((name, float(dur) if dur else DEFAULT_DURATION_S))
    if not out:
        raise ValueError("empty scene script")
    return out


class ScenePlayer:
    """Plays a scene script; `render(frame_index)` returns (canvas, scene_name, targets)."""

    def __init__(self, script: str | list[tuple[str, float]], fps: int = 30) -> None:
        self.fps = fps
        self.entries = parse_script(script) if isinstance(script, str) else list(script)
        self._scenes: dict[str, Scene] = {}
        self.total_s = sum(d for _, d in self.entries)
        for name, dur in self.entries:  # build every scene up front so no frame pays the setup cost
            self._scene(name).duration_s = dur

    def _scene(self, name: str) -> Scene:
        if name not in self._scenes:
            self._scenes[name] = _SCENES[name](self.fps)
        return self._scenes[name]

    def locate(self, t: float) -> tuple[str, float]:
        """Scene name and time within the scene at script time `t` (the script loops)."""
        t = t % self.total_s if self.total_s > 0 else 0.0
        for name, dur in self.entries:
            if t < dur:
                return name, t
            t -= dur
        name, dur = self.entries[-1]
        return name, dur

    def render(self, index: int) -> tuple[np.ndarray, str, list[Target]]:
        name, t_in = self.locate(index / self.fps)
        frame, targets = self._scene(name).render(t_in)
        return frame, name, targets

    def targets(self, index: int) -> tuple[str, list[Target]]:
        """Scene name and ground-truth targets of frame `index`, without rendering pixels."""
        name, t_in = self.locate(index / self.fps)
        return name, self._scene(name).targets_at(t_in)
