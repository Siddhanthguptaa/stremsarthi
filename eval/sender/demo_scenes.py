"""Scenes of the judge demo reel (``--script demo_reel``, 90 s): SPECIMEN data only, nothing real.

The reel cycles through one scene per privacy category so a judge sees each mask appear and disappear:

    Aadhaar-style card (still, then moving) -> PAN-style card -> UPI QR -> Indian number plate -> Hindi
    signboard -> phone with an OTP notification -> BGMI-style lobby (player id, room id, password) ->
    an ENROLLED face next to a stranger (they swap sides halfway) -> a calm scene (nothing to hide).

Everything is drawn by the existing generators in ``eval/data_gen/video`` (cards, QR poster, plates,
signboards, phone screens, game lobbies, NASA public-domain portraits) and placed with the same perspective code
the gate scenes use; nothing is duplicated here except the placement poses and the caption. Every scene carries a
small caption in the bottom-left corner ("SPECIMEN Aadhaar", ...) inside a reserved band (``CAPTION_Y0`` and
below) that no target ever enters; a test checks every scene at every sampled time. The optical stamp stays in the
top-left corner as in every sender scene.

Ground truth categories follow the gate scenes (``aadhaar``, ``pan``, ``upi_qr``, ``plate``, ``signboard_text``,
``game_*``) plus ``face`` (a stranger: must be masked) and ``face_enrolled`` (on the allowlist: must stay visible).
The face scene needs the NASA portraits in ``eval/data/generated/faces/src`` (``scripts/demo_assets.sh`` fetches
and verifies them).
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..data_gen.common.fonts import load_font
from ..data_gen.video import compose, games, plates, screens, signboards
from ..data_gen.video import faces as faces_mod
from ..data_gen.video.cards import render_pan
from ..data_gen.video.compose import Sprite, SpriteTarget, rect_quad
from ..data_gen.video.qr import render_qr_poster
from .scenes import PRESETS, CardMoving, CardStatic, Idle, Scene, Target, register_scene
from .stamp import CANVAS_H, CANVAS_W, STAMP_EXCLUSION

DEMO_SEED = 4242
#: rows below this y are reserved for the caption; no target ever reaches into them
CAPTION_Y0 = 668
CAPTION_BOX_H = 38
DEMO_UPI_ID = "demo.streamer.zz@ybl"
DEMO_UPI_PAYLOAD = f"upi://pay?pa={DEMO_UPI_ID}&pn=SPECIMEN%20DEMO&am=1&cu=INR"
DEMO_PLATE = "KA03ZZ4821"
FACES_CACHE = Path(__file__).resolve().parents[2] / "eval" / "data" / "generated" / "faces" / "src"

#: (scene name, seconds, caption); the order is the reel
REEL: tuple[tuple[str, float, str], ...] = (
    ("demo_aadhaar_static", 7.0, "SPECIMEN Aadhaar (still)"),
    ("demo_aadhaar_moving", 8.0, "SPECIMEN Aadhaar (moving)"),
    ("demo_pan", 7.0, "SPECIMEN PAN card"),
    ("demo_upi_qr", 8.0, "SPECIMEN UPI QR"),
    ("demo_plate", 8.0, "SPECIMEN number plate"),
    ("demo_signboard", 8.0, "Hindi signboard (SPECIMEN)"),
    ("demo_otp_phone", 9.0, "SPECIMEN phone with an OTP"),
    ("demo_bgmi_lobby", 9.0, "SPECIMEN BGMI lobby"),
    ("demo_faces", 14.0, "Enrolled face + stranger"),
    ("demo_calm", 12.0, "Calm scene: nothing to hide"),
)
DEMO_REEL_SCRIPT = ",".join(f"{n}:{d:g}" for n, d, _ in REEL)
DEMO_REEL_SECONDS = sum(d for _, d, _ in REEL)
PRESETS["demo_reel"] = DEMO_REEL_SCRIPT


def reel_schedule() -> list[dict[str, Any]]:
    """``[{"scene", "t0", "t1", "caption"}, ...]`` for the audio timeline and the demo script."""
    out, t = [], 0.0
    for name, dur, caption in REEL:
        out.append({"scene": name, "t0": t, "t1": t + dur, "caption": caption})
        t += dur
    return out


def _caption_for(name: str) -> str:
    return next(c for n, _, c in REEL if n == name)


def caption_box(text: str) -> tuple[int, int, int, int]:
    """Pixel box (x1, y1, x2, y2) the caption occupies; always inside the reserved band."""
    width = int(load_font("latin", 24, 700).getlength(text))
    return (8, CAPTION_Y0 + 6, 8 + width + 24, CAPTION_Y0 + 6 + CAPTION_BOX_H)


def draw_caption(frame: np.ndarray, text: str) -> None:
    """Draw the caption into `frame` (RGB, in place), bottom-left, white on black."""
    x1, y1, x2, y2 = caption_box(text)
    region = Image.fromarray(np.ascontiguousarray(frame[y1 - 2 : y2 + 2, 0 : x2 + 4]))
    d = ImageDraw.Draw(region)
    d.rectangle((x1, 2, x2, y2 - y1 + 2), fill=(0, 0, 0))
    d.text(
        (x1 + 12, 2 + CAPTION_BOX_H // 2),
        text,
        font=load_font("latin", 24, 700),
        fill=(255, 255, 255),
        anchor="lm",
    )
    frame[y1 - 2 : y2 + 2, 0 : x2 + 4] = np.asarray(region)


class _Captioned:
    """Mixin: draw the scene's caption on whatever the base scene renders."""

    name: str

    def render(self, t: float):  # type: ignore[no-untyped-def]
        frame, targets = super().render(t)  # type: ignore[misc]
        draw_caption(frame, _caption_for(self.name))
        return frame, targets


def _bbox(poly: Any) -> tuple[int, int, int, int]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return (
        int(math.floor(min(xs))),
        int(math.floor(min(ys))),
        int(math.ceil(max(xs))),
        int(math.ceil(max(ys))),
    )


def _target(category: str, poly: Any, rotated: bool = True) -> Target:
    corners = tuple((float(x), float(y)) for x, y in poly) if rotated else None
    return Target(category, _bbox(poly), corners=corners)


# ---- reused card scenes -----------------------------------------------------------------------------------
@register_scene
class DemoAadhaarStatic(_Captioned, CardStatic):
    name = "demo_aadhaar_static"


@register_scene
class DemoAadhaarMoving(_Captioned, CardMoving):
    name = "demo_aadhaar_moving"

    def setup(self) -> None:
        super().setup()  # same Lissajous hand motion as card_moving, but clear of the caption band
        # the reel keeps its original path (top 56 px: the demo targets are pinned by a test); card_moving itself
        # now keeps STAMP_CLEARANCE below the stamp, which this scene does not (see the report to the lead)
        self.y_range = (STAMP_EXCLUSION[3] + 16 + 8, CAPTION_Y0 - self.h - 8)


@register_scene
class DemoCalm(_Captioned, Idle):
    name = "demo_calm"


# ---- generator sprites placed with the shared perspective code --------------------------------------------
class _SpriteScene(_Captioned, Scene):
    """One generator sprite, gently swaying (roll, yaw, centre) so tracking has something to do."""

    width_px = 560.0
    centre = (640.0, 340.0)
    sway = (3.0, 8.0, 40.0)  # roll deg, yaw deg, centre x px
    period_s = 6.0

    def make_sprite(self, rng: random.Random) -> Sprite:  # pragma: no cover - overridden
        raise NotImplementedError

    def setup(self) -> None:
        self.sprite = self.make_sprite(random.Random(DEMO_SEED))
        h, w = self.sprite.image.shape[:2]
        self.size = (w, h)
        self.src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)

    def _dst(self, t: float) -> compose.Placement:
        ph = 2 * math.pi * t / self.period_s
        roll, yaw, dx = self.sway
        c = (self.centre[0] + dx * math.sin(ph), self.centre[1])
        r, y = roll * math.sin(ph + 0.8), yaw * math.sin(ph)
        dst = compose.projected_quad(self.size, c, self.width_px, r, y, 0.0)
        return compose.Placement(c, self.width_px, r, y, 0.0, dst)

    def targets_at(self, t: float) -> list[Target]:
        pl = self._dst(t)
        m = cv2.getPerspectiveTransform(self.src, pl.dst.astype(np.float32))
        out = []
        for st in self.sprite.targets:
            q = compose.transform_quad(m, st.quad)
            out.append(_target(st.category, q))
        return out

    def render(self, t: float):
        pl = self._dst(t)
        img, _h, _a = compose.warp(self.base.copy(), self.sprite, pl)
        draw_caption(img, _caption_for(self.name))
        return img, self.targets_at(t)


@register_scene
class DemoPan(_SpriteScene):
    name = "demo_pan"
    width_px = 560.0

    def make_sprite(self, rng: random.Random) -> Sprite:
        card = render_pan(rng)
        h, w = card.image.shape[:2]
        return Sprite(card.image, [SpriteTarget("pan", rect_quad((0, 0, w, h)))])


@register_scene
class DemoUpiQr(_SpriteScene):
    name = "demo_upi_qr"
    width_px = 360.0

    def make_sprite(self, rng: random.Random) -> Sprite:
        poster = render_qr_poster(rng, payload=DEMO_UPI_PAYLOAD)
        return Sprite(poster.image, [SpriteTarget("upi_qr", rect_quad(poster.qr_bbox))])


@register_scene
class DemoPlate(_SpriteScene):
    name = "demo_plate"
    width_px = 860.0
    centre = (640.0, 335.0)
    sway = (2.0, 10.0, 30.0)

    def make_sprite(self, rng: random.Random) -> Sprite:
        return plates.render_vehicle(rng, DEMO_PLATE, "car", scheme="private")


@register_scene
class DemoSignboard(_SpriteScene):
    name = "demo_signboard"
    width_px = 900.0
    centre = (640.0, 320.0)
    sway = (2.0, 7.0, 40.0)

    def make_sprite(self, rng: random.Random) -> Sprite:
        return signboards.render_signboard(rng, "devanagari")


@register_scene
class DemoOtpPhone(_SpriteScene):
    name = "demo_otp_phone"
    width_px = 290.0  # the phone is 440 x 940: about 620 px tall
    centre = (640.0, 340.0)
    sway = (3.0, 14.0, 60.0)

    def make_sprite(self, rng: random.Random) -> Sprite:
        return screens.render_otp_screen(rng, layout="lock")


@register_scene
class DemoBgmiLobby(_Captioned, Scene):
    """A full-screen BGMI-style custom-room lobby: player id, room id and password are the targets."""

    name = "demo_bgmi_lobby"

    def setup(self) -> None:
        self.sprite = games.render_lobby(random.Random(DEMO_SEED), "bgmi", with_credentials=True)
        self.targets = [_target(t.category, t.quad, rotated=False) for t in self.sprite.targets]

    def render(self, t: float):
        frame = self.sprite.image.copy()
        draw_caption(frame, _caption_for(self.name))
        return frame, list(self.targets)

    def targets_at(self, t: float) -> list[Target]:
        return list(self.targets)


@register_scene
class DemoFaces(_Captioned, Scene):
    """An enrolled face next to a stranger (NASA public-domain portraits); they swap sides halfway through."""

    name = "demo_faces"
    TILE = (560, 572)
    LEFT_XY = (40, 60)
    RIGHT_XY = (680, 60)

    def setup(self) -> None:
        enrolled = faces_mod.sources_for(use="enroll", person_id="P01")[0]
        stranger = faces_mod.sources_for(role="stranger", person_id="S01")[0]
        cache = str(FACES_CACHE)
        rng = random.Random(DEMO_SEED)
        self.phases: list[tuple[np.ndarray, list[Target]]] = []
        for left, right in ((enrolled, stranger), (stranger, enrolled)):
            canvas = np.full((CANVAS_H, CANVAS_W, 3), 22, np.uint8)
            targets: list[Target] = []
            for src, (ox, oy) in ((left, self.LEFT_XY), (right, self.RIGHT_XY)):
                tile, quad = faces_mod.render_tile(cache, src, self.TILE, 0.42, rng, angle=0.0, mirror=False)
                canvas[oy : oy + self.TILE[1], ox : ox + self.TILE[0]] = tile
                label = "ENROLLED (stays visible)" if src is enrolled else "STRANGER (masked)"
                im = Image.fromarray(canvas)
                ImageDraw.Draw(im).text(
                    (ox + self.TILE[0] // 2, oy + self.TILE[1] + 16), label,
                    font=load_font("latin", 22, 600), fill=(235, 235, 235), anchor="mm",
                )  # fmt: skip
                canvas = np.asarray(im).copy()
                poly = [(ox + x, oy + y) for x, y in quad]
                targets.append(_target("face_enrolled" if src is enrolled else "face", poly))
            self.phases.append((canvas, targets))

    def _phase(self, t: float) -> int:
        return 0 if (t % max(self.duration_s, 1.0)) < self.duration_s / 2 else 1

    def render(self, t: float):
        canvas, targets = self.phases[self._phase(t)]
        frame = canvas.copy()
        draw_caption(frame, _caption_for(self.name))
        return frame, list(targets)

    def targets_at(self, t: float) -> list[Target]:
        return list(self.phases[self._phase(t)][1])


def demo_player(fps: int = 30):  # type: ignore[no-untyped-def]
    """A ``ScenePlayer`` for the reel (a convenience for tests and scripts)."""
    from .scenes import ScenePlayer

    return ScenePlayer(DEMO_REEL_SCRIPT, fps)


__all__ = [
    "CAPTION_Y0",
    "DEMO_PLATE",
    "DEMO_REEL_SCRIPT",
    "DEMO_REEL_SECONDS",
    "DEMO_UPI_ID",
    "DEMO_UPI_PAYLOAD",
    "REEL",
    "STAMP_EXCLUSION",
    "caption_box",
    "demo_player",
    "draw_caption",
    "reel_schedule",
]
