"""The adversarial attack catalogue: ``cases(seed, ...)`` yields reproducible ``Case`` objects.

Every attack is a function ``(seed, label, params) -> list[Frame]`` plus a parameter grid. Frames are rendered
lazily when the case runs (a case is at most ~100 frames, about 250 MB if held; they are dropped after scoring).
All artwork is SPECIMEN data (see ``scene.py``). Faces use the cached CC0 NASA portraits of the T2.1 suite.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ...data_gen.common import identifiers, specimen
from ...data_gen.common.seeds import np_rng, py_rng
from ...data_gen.video import faces as faces_mod
from ...data_gen.video.compose import Sprite, SpriteTarget
from . import scene, textscenes
from .model import Case, Frame
from .scene import CENTER, H, W
from .textscenes import Seg

ROOT = Path(__file__).resolve().parents[3]
FACES_CACHE = ROOT / "eval" / "data" / "generated" / "faces" / "src"
STRIDE = 3  # detect.stride of the dev profile; the "slots" frame-drop pattern targets exactly these frames

Builder = Callable[[int, str, dict[str, Any]], list[Frame]]


@dataclass(frozen=True)
class Attack:
    name: str
    group: str
    grid: dict[str, dict[str, Any]]  # label -> params
    build: Builder
    quick: tuple[str, ...]  # labels run by --quick (one mid-severity case, or two for different objects)
    note: str = ""
    requires: Callable[[], str | None] = field(
        default=lambda: None, compare=False
    )  # reason when not runnable


# ---------------------------------------------------------------------------------------------- shared helpers
def _obj(seed: int, obj: str, tag: str) -> tuple[Sprite, float]:
    if obj in ("aadhaar", "pan"):
        return scene.sprite_card(seed, obj, tag), 0.42 * W
    if obj == "qr":
        return scene.sprite_qr(seed, tag), 0.27 * W
    if obj == "plate":
        return scene.sprite_plate(seed, tag), 0.55 * W
    return scene.sprite_text(seed, obj, tag), 0.9 * W  # phone | upi_id | email | pin


def _static(
    seed: int,
    name: str,
    label: str,
    obj: str,
    *,
    n: int = 9,
    width: float | None = None,
    center: tuple[float, float] = CENTER,
    roll: float = 0.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    make_aug: Callable[[np.random.Generator], Any] | None = None,
    sprite: Sprite | None = None,
    bg_style: str | None = None,
) -> list[Frame]:
    g = np_rng(seed, name, label)
    sp, w0 = (sprite, 0.42 * W) if sprite is not None else _obj(seed, obj, f"{name}-{label}")
    width = width if width is not None else w0
    bg = scene.backdrop(seed, f"{name}-{label}", bg_style)
    out = []
    for _ in range(n):
        dx, dy, dr = scene.jitter_pose(g)
        pl = scene.placement(sp, (center[0] + dx, center[1] + dy), width, roll + dr, yaw, pitch)
        out.append(scene.render_frame(bg, sp, pl, make_aug(g) if make_aug else scene.aug(), g))
    return out


# ---------------------------------------------------------------------------------------------- attacks
def b_control(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    return _static(seed, "control", label, p["obj"])


def b_occlusion(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    return _static(
        seed,
        "occlusion",
        label,
        p["obj"],
        make_aug=lambda g: scene.aug(occlusion=p["occ"], side=p["side"]),
    )


def b_tilt(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    return _static(
        seed, "tilt", label, p["obj"], yaw=p["yaw"], pitch=p.get("pitch", 0.0), roll=p.get("roll", 0.0)
    )


def b_distance(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    return _static(seed, "distance", label, p["obj"], width=p["w"] * W)


def b_glare(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    def mk(g: np.random.Generator) -> Any:
        gl = [scene.glare_on(p["cx"], p["cy"], p["strength"], p.get("rx", 0.5), p.get("ry", 0.5))]
        return scene.aug(glare=gl)

    return _static(seed, "glare", label, p["obj"], make_aug=mk)


def _nested(seed: int, name: str, label: str, p: dict[str, Any]) -> list[Frame]:
    inner, _ = _obj(seed, p["obj"], f"{name}-{label}-in")
    sp = scene.nested_sprite(
        seed,
        inner,
        kind=p["kind"],
        inner_width=p["inner_w"] * W,
        inner_yaw=p["inner_yaw"],
        inner_roll=p["inner_roll"],
        tag=f"{name}-{label}",
    )
    return _static(
        seed,
        name,
        label,
        p["obj"],
        sprite=sp,
        width=p["w"] * W,
        yaw=p["yaw"],
        roll=p["roll"],
        pitch=p.get("pitch", 0.0),
        make_aug=lambda g: scene.aug(
            jpeg=p["jpeg"],
            defocus=p["blur"],
            blur="defocus",
            gain=p.get("gain", 1.0),
            glare=[scene.glare_on(0.4, 0.4, 0.6)] if p.get("glare") else [],
        ),
    )


def b_screen(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    return _nested(seed, "screen_of_screen", label, p)


def b_photo(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    return _nested(seed, "photo_of_photo", label, p)


def b_pan(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    g = np_rng(seed, "motion_pan", label)
    sp, w0 = _obj(seed, p["obj"], f"pan-{label}")
    bg = scene.backdrop(seed, f"pan-{label}")
    v, n = p["v"], 30
    lo, hi = 0.28 * W, 0.72 * W
    span = hi - lo
    out = []
    for k in range(n):
        s = (v * k) % (2 * span)
        x = lo + (s if s <= span else 2 * span - s)
        pl = scene.placement(sp, (x, H * 0.5 + g.normal(0, 3)), w0, g.normal(0, 0.6), 0.0, 0.0)
        out.append(
            scene.render_frame(
                bg, sp, pl, scene.aug(blur="motion", motion_len=max(3, int(v * 0.45)), motion_angle=0.0), g
            )
        )
    return out


def b_shake(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    g = np_rng(seed, "motion_shake", label)
    sp, w0 = _obj(seed, p["obj"], f"shake-{label}")
    bg = scene.backdrop(seed, f"shake-{label}")
    out = []
    for _ in range(30):
        c = (W / 2 + g.normal(0, p["amp"]), H / 2 + g.normal(0, p["amp"] * 0.6))
        pl = scene.placement(sp, c, w0, g.normal(0, p["amp"] / 5), g.normal(0, 3), g.normal(0, 3))
        out.append(
            scene.render_frame(
                bg, sp, pl, scene.aug(blur="motion", motion_len=9, motion_angle=float(g.uniform(0, 180))), g
            )
        )
    return out


def b_enter_leave(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    g = np_rng(seed, "enter_leave", label)
    sp, w0 = _obj(seed, p["obj"], f"el-{label}")
    bg = scene.backdrop(seed, f"el-{label}")
    v = p["v"]
    if p.get("hold") is not None:  # card parked with only a fraction inside the frame edge
        x = -w0 / 2 + p["hold"] * w0
        return [
            scene.render_frame(bg, sp, scene.placement(sp, (x + g.normal(0, 2), H / 2), w0), scene.aug(), g)
            for _ in range(15)
        ]
    n = int(math.ceil((W + w0) / v)) + 3
    out = []
    for k in range(n):
        x = W + w0 / 2 - v * k  # right edge in, left edge out
        pl = scene.placement(sp, (x, H / 2 + g.normal(0, 3)), w0, g.normal(0, 0.5))
        out.append(
            scene.render_frame(
                bg, sp, pl, scene.aug(blur="motion", motion_len=max(3, int(v * 0.3)), motion_angle=0.0), g
            )
        )
    return out


def b_flash(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    g = np_rng(seed, "flash", label)
    sp, w0 = _obj(seed, p["obj"], f"flash-{label}")
    bg = scene.backdrop(seed, f"flash-{label}")
    start, dur, n = 22, p["frames"], 60  # frame 22 is not a multiple of the detect stride (3)
    out = []
    for k in range(n):
        if start <= k < start + dur:
            dx, dy, dr = scene.jitter_pose(g)
            out.append(
                scene.render_frame(
                    bg,
                    sp,
                    scene.placement(sp, (W / 2 + dx, H / 2 + dy), w0, dr),
                    scene.aug(),
                    g,
                    tag="visible",
                )
            )
        else:
            out.append(scene.empty_frame(bg, g, tag="hidden"))
    return out


def b_cut(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    g = np_rng(seed, "scene_cut", label)
    rng = py_rng(seed, "scene_cut", label)
    out: list[Frame] = []
    period, n = p["period"], 60
    sp, w0 = _obj(seed, p["obj"], f"cut-{label}-0")
    bg = scene.backdrop(seed, f"cut-{label}-0")
    pose = (W / 2, H / 2, 0.0, 0.0)
    for k in range(n):
        since = k % period
        if since == 0:  # a hard cut: new backdrop, new artwork (new number), new pose
            idx = k // period
            sp, w0 = _obj(seed, p["obj"], f"cut-{label}-{idx}")
            bg = scene.backdrop(seed, f"cut-{label}-{idx}")
            pose = (
                rng.uniform(0.3, 0.7) * W,
                rng.uniform(0.4, 0.6) * H,
                rng.uniform(-8, 8),
                rng.uniform(-18, 18),
            )
        dx, dy, dr = scene.jitter_pose(g)
        pl = scene.placement(sp, (pose[0] + dx, pose[1] + dy), w0, pose[2] + dr, pose[3])
        out.append(
            scene.render_frame(bg, sp, pl, scene.aug(), g, tag=f"cut+{since}" if since < 6 else "steady")
        )
    return out


def b_drops(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    g = np_rng(seed, "frame_drops", label)
    rng = py_rng(seed, "frame_drops", label)
    sp, w0 = _obj(seed, p["obj"], f"drop-{label}")
    bg = scene.backdrop(seed, f"drop-{label}")
    n = 60
    out = []
    for k in range(n):
        x = 0.3 * W + 6.0 * k  # drifting right, so a stale mask is displaced from the card
        pl = scene.placement(sp, (x, H / 2), w0, 0.0, 6.0 * math.sin(k / 9.0))
        f = scene.render_frame(bg, sp, pl, scene.aug(blur="motion", motion_len=5, motion_angle=0.0), g)
        pat = p["pattern"]
        f.dropped = (
            (pat == "slots" and k % STRIDE == 0)
            or (pat == "burst" and 20 <= k < 35)
            or (pat == "random" and rng.random() < 0.3)
        )
        out.append(f)
    return out


# ---- text attacks --------------------------------------------------------------------------------------------
def _phone(seed: int, label: str) -> str:
    return specimen.phone_number(py_rng(seed, "rt-phone", label))


def _text_frames(
    seed: int, name: str, label: str, sprite: Sprite, *, roll: float = 0.0, n: int = 9
) -> list[Frame]:
    return _static(seed, name, label, "text", n=n, sprite=sprite, width=0.92 * W, roll=roll)


def b_fonts(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    rng = py_rng(seed, "fonts", label)
    font = p["font"]
    value = _phone(seed, label)
    size = p.get("size", 40)
    if font == "spaced":  # letter-spaced digits
        seg = Seg(value, mark=("text_phone", value), spacing=14.0)
    elif font == "rotated":
        seg = Seg(value, mark=("text_phone", value))
    else:
        seg = Seg(value, font=font, mark=("text_phone", value))
    sp = textscenes.screen(rng, [([Seg("Call me on: "), seg], size)])
    return _text_frames(seed, "fonts", label, sp, roll=p.get("roll", 0.0))


def b_mixed_script(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    rng = py_rng(seed, "mixed", label)
    v = _phone(seed, label)
    kind = p["variant"]
    if kind == "hi_latin_digits":
        segs = [Seg("मेरा नंबर ", "devanagari"), Seg(v, mark=("text_phone", v)), Seg(" है", "devanagari")]
    elif kind == "hi_deva_digits":
        segs = [
            Seg("मेरा नंबर ", "devanagari"),
            Seg(identifiers.to_devanagari_digits(v), "devanagari", mark=("text_phone", v)),
        ]
    elif kind == "bn_digits":
        bn = v.translate({ord(str(i)): 0x09E6 + i for i in range(10)})
        segs = [Seg("আমার নম্বর ", "bengali"), Seg(bn, "bengali", mark=("text_phone", v))]
    elif kind == "ta_digits":
        ta = v.translate({ord(str(i)): 0x0BE6 + i for i in range(10)})
        segs = [Seg("என் எண் ", "tamil"), Seg(ta, "tamil", mark=("text_phone", v))]
    elif kind == "mixed_in_number":  # one number whose digits switch script half way
        segs = [
            Seg("call ", "latin"),
            Seg(v[:5], mark=("text_phone", v)),
            Seg(identifiers.to_devanagari_digits(v[5:]), "devanagari", mark=("text_phone", v)),
        ]
    else:  # hinglish_upi: a UPI id inside a Devanagari sentence
        u = specimen.upi_id(rng, rng.choice(specimen.UPI_HANDLES))
        segs = [Seg("मेरा UPI ", "devanagari"), Seg(u, mark=("text_upi_id", u))]
    return _text_frames(seed, "mixed_script", label, textscenes.screen(rng, [(segs, 40)]))


def b_split_lines(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    rng = py_rng(seed, "split", label)
    kind = p["variant"]
    if kind in ("phone_hyphen", "phone_5_5"):
        v = _phone(seed, label)
        a, b = v[:5], v[5:]
        l1 = [Seg("Reach me at "), Seg(a + ("-" if kind == "phone_hyphen" else ""), mark=("text_phone", a))]
        l2 = [Seg(b, mark=("text_phone", b)), Seg(" after 6 pm")]
        lines = [(l1, 40), (l2, 40)]
    elif kind == "upi_at":
        u = specimen.upi_id(rng, "oksbi")
        a, b = u.split("@")
        lines = [
            ([Seg("Pay to "), Seg(a + "@", mark=("text_upi_id", a))], 40),
            ([Seg(b, mark=("text_upi_id", b))], 40),
        ]
    elif kind == "email":
        e = identifiers.email_address(rng)
        a, b = e.split("@")
        lines = [
            ([Seg("Mail "), Seg(a + "@", mark=("text_email", a))], 40),
            ([Seg(b, mark=("text_email", b))], 40),
        ]
    else:  # aadhaar_3_lines: a typed 12-digit number broken over three lines (4 + 4 + 4)
        d = "".join(
            rng.choice("01") + "".join(rng.choice("0123456789") for _ in range(3)) for _ in range(1)
        ) + "".join(rng.choice("0123456789") for _ in range(8))
        parts = [d[:4], d[4:8], d[8:]]
        lines = [
            ([Seg("ID: " if i == 0 else ""), Seg(part, mark=("aadhaar", part))], 40)
            for i, part in enumerate(parts)
        ]
    return _text_frames(seed, "split_lines", label, textscenes.screen(rng, lines))


def b_separators(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    rng = py_rng(seed, "separators", label)
    v = _phone(seed, label)
    a, b = v[:5], v[5:]
    shown = {
        "hyphen": f"{a}-{b}",
        "dots": f"{a}.{b}",
        "slash": f"{a}/{b}",
        "all_spaced": " ".join(v),
        "parens": f"({a}) {b}",
        "plus91_mixed": f"+91 ({v[:3]}) {v[3:6]}-{v[6:]}",
        "zero_width": "​".join(v),
        "words_en": " ".join("zero one two three four five six seven eight nine".split()[int(c)] for c in v),
    }[p["variant"]]
    expect = shown if p["variant"] == "words_en" else v
    sp = textscenes.screen(rng, [([Seg("Whatsapp: "), Seg(shown, mark=("text_phone", expect))], 42)])
    return _text_frames(seed, "separators", label, sp)


# ---- faces ----------------------------------------------------------------------------------------------------
def _face_ready() -> str | None:
    if not FACES_CACHE.exists() or not any(FACES_CACHE.glob("*.jpg")):
        return f"no face cache at {FACES_CACHE} (python -m eval.run gen-video fetches it)"
    return None


def _face_sprite(seed: int, label: str, accessory: str) -> Sprite:
    rng = py_rng(seed, "face", label)
    pool = faces_mod.sources_for(role="stranger")
    src = pool[rng.randrange(len(pool))]
    tile, quad = faces_mod.render_tile(str(FACES_CACHE), src, (520, 520), 0.42, rng, angle=0.0, mirror=False)
    xs, ys = [q[0] for q in quad], [q[1] for q in quad]
    fx, fy, fw, fh = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    t = tile.copy()
    if accessory == "sunglasses":
        cv2.rectangle(
            t,
            (int(fx + 0.04 * fw), int(fy + 0.30 * fh)),
            (int(fx + 0.96 * fw), int(fy + 0.50 * fh)),
            (18, 18, 22),
            -1,
        )
    elif accessory == "mask":
        cv2.rectangle(
            t,
            (int(fx + 0.02 * fw), int(fy + 0.55 * fh)),
            (int(fx + 0.98 * fw), int(fy + fh)),
            (170, 205, 225),
            -1,
        )
    return Sprite(t, [SpriteTarget("face", quad, {"allowlisted": False, "person_id": src.person_id})])


def b_face(seed: int, label: str, p: dict[str, Any]) -> list[Frame]:
    sp = _face_sprite(seed, label, p.get("accessory", ""))
    c = p.get("center", (0.5, 0.5))  # fractions of the frame
    return _static(
        seed,
        "face",
        label,
        "face",
        sprite=sp,
        width=p.get("w", 0.34) * W,
        center=(c[0] * W, c[1] * H),
        yaw=p.get("yaw", 0.0),
        bg_style="wall",
    )


# ---------------------------------------------------------------------------------------------- catalogue
def _grid(**labels: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return labels


def _build_catalogue() -> dict[str, Attack]:
    a: list[Attack] = []
    a.append(
        Attack(
            "control",
            "control",
            _grid(
                aadhaar={"obj": "aadhaar"},
                pan={"obj": "pan"},
                qr={"obj": "qr"},
                phone={"obj": "phone"},
                plate={"obj": "plate"},
            ),
            b_control,
            ("aadhaar", "qr", "phone"),
            "clean frontal targets: the harness floor, a miss here is a detector regression",
        )
    )
    a.append(
        Attack(
            "occlusion_card",
            "occlusion",
            _grid(
                occ20_right={"obj": "aadhaar", "occ": 0.2, "side": "right"},
                occ40_left={"obj": "aadhaar", "occ": 0.4, "side": "left"},
                occ60_bottom={"obj": "aadhaar", "occ": 0.6, "side": "bottom"},
                occ60_top={"obj": "pan", "occ": 0.6, "side": "top"},
            ),
            b_occlusion,
            ("occ40_left", "occ60_bottom"),
        )
    )
    a.append(
        Attack(
            "occlusion_qr",
            "occlusion",
            _grid(
                occ15_right={"obj": "qr", "occ": 0.15, "side": "right"},
                occ30_bottom={"obj": "qr", "occ": 0.3, "side": "bottom"},
                occ45_left={"obj": "qr", "occ": 0.45, "side": "left"},
            ),
            b_occlusion,
            ("occ30_bottom",),
        )
    )
    a.append(
        Attack(
            "tilt_card",
            "angle",
            _grid(
                yaw35={"obj": "aadhaar", "yaw": 35.0, "pitch": 10.0},
                yaw50={"obj": "pan", "yaw": 50.0, "pitch": 10.0},
                yaw65={"obj": "aadhaar", "yaw": 65.0, "pitch": 12.0},
                yaw72={"obj": "pan", "yaw": 72.0, "pitch": 5.0},
            ),
            b_tilt,
            ("yaw50", "yaw65"),
        )
    )
    a.append(
        Attack(
            "tilt_qr",
            "angle",
            _grid(
                yaw40={"obj": "qr", "yaw": 40.0, "pitch": 20.0},
                yaw60={"obj": "qr", "yaw": 60.0, "pitch": 30.0},
                yaw70={"obj": "qr", "yaw": 70.0, "pitch": 0.0},
            ),
            b_tilt,
            ("yaw60",),
        )
    )
    a.append(
        Attack(
            "tilt_plate",
            "angle",
            _grid(yaw40={"obj": "plate", "yaw": 40.0}, yaw60={"obj": "plate", "yaw": 60.0}),
            b_tilt,
            ("yaw60",),
        )
    )
    a.append(
        Attack(
            "distance_card",
            "distance",
            _grid(
                w10={"obj": "aadhaar", "w": 0.10},
                w06={"obj": "pan", "w": 0.06},
                w04={"obj": "aadhaar", "w": 0.04},
            ),
            b_distance,
            ("w06",),
        )
    )
    a.append(
        Attack(
            "distance_qr",
            "distance",
            _grid(w10={"obj": "qr", "w": 0.10}, w06={"obj": "qr", "w": 0.06}, w04={"obj": "qr", "w": 0.04}),
            b_distance,
            ("w06",),
        )
    )
    a.append(
        Attack(
            "glare_card",
            "glare",
            _grid(
                s80={"obj": "aadhaar", "cx": 0.5, "cy": 0.55, "strength": 0.8, "rx": 0.6, "ry": 0.5},
                s100={"obj": "pan", "cx": 0.5, "cy": 0.55, "strength": 1.0, "rx": 0.7, "ry": 0.6},
            ),
            b_glare,
            ("s100",),
        )
    )
    a.append(
        Attack(
            "glare_qr",
            "glare",
            _grid(
                s70={"obj": "qr", "cx": 0.5, "cy": 0.5, "strength": 0.7, "rx": 0.5, "ry": 0.5},
                s95={"obj": "qr", "cx": 0.5, "cy": 0.5, "strength": 0.95, "rx": 0.6, "ry": 0.6},
            ),
            b_glare,
            ("s95",),
        )
    )
    scr = dict(
        kind="screen",
        inner_w=0.5,
        inner_yaw=15.0,
        inner_roll=4.0,
        yaw=12.0,
        roll=5.0,
        w=0.62,
        jpeg=70,
        blur=1.0,
    )
    a.append(
        Attack(
            "screen_of_screen",
            "screens_of_screens",
            _grid(
                card_mild={"obj": "aadhaar", **scr},
                card_strong={
                    "obj": "pan",
                    **scr,
                    "yaw": 28.0,
                    "blur": 1.9,
                    "glare": True,
                    "jpeg": 50,
                    "gain": 0.8,
                },
                qr_mild={"obj": "qr", **scr, "inner_w": 0.34},
                qr_strong={
                    "obj": "qr",
                    **scr,
                    "inner_w": 0.3,
                    "yaw": 28.0,
                    "blur": 1.9,
                    "glare": True,
                    "jpeg": 50,
                },
            ),
            b_screen,
            ("card_strong", "qr_mild"),
            "card or QR shown on a monitor that is itself filmed: bezel, moire, blur, glare",
        )
    )
    pho = dict(
        kind="print",
        inner_w=0.5,
        inner_yaw=10.0,
        inner_roll=3.0,
        yaw=18.0,
        roll=-8.0,
        w=0.62,
        jpeg=60,
        blur=1.4,
    )
    a.append(
        Attack(
            "photo_of_photo",
            "photo_of_photo",
            _grid(
                card={"obj": "aadhaar", **pho},
                card_far={"obj": "pan", **pho, "w": 0.4, "blur": 1.8},
                qr={"obj": "qr", **pho, "inner_w": 0.32},
            ),
            b_photo,
            ("card", "qr"),
            "a printed photo of the card/QR on a desk, warm cast, glossy streak",
        )
    )
    a.append(
        Attack(
            "motion_pan_card",
            "motion",
            _grid(
                v20={"obj": "aadhaar", "v": 20.0},
                v50={"obj": "pan", "v": 50.0},
                v100={"obj": "aadhaar", "v": 100.0},
            ),
            b_pan,
            ("v50",),
        )
    )
    a.append(Attack("motion_pan_qr", "motion", _grid(v50={"obj": "qr", "v": 50.0}), b_pan, ("v50",)))
    a.append(
        Attack(
            "motion_shake_card",
            "motion",
            _grid(amp15={"obj": "aadhaar", "amp": 15.0}, amp40={"obj": "pan", "amp": 40.0}),
            b_shake,
            ("amp40",),
        )
    )
    a.append(
        Attack(
            "enter_leave_card",
            "enter_leave",
            _grid(
                slow={"obj": "aadhaar", "v": 18.0},
                fast={"obj": "pan", "v": 45.0},
                edge_hold35={"obj": "aadhaar", "v": 0.0, "hold": 0.35},
            ),
            b_enter_leave,
            ("fast", "edge_hold35"),
            "card slides in from the right edge and out the left; edge_hold35 parks it 35% inside the frame",
        )
    )
    a.append(
        Attack(
            "enter_leave_qr", "enter_leave", _grid(fast={"obj": "qr", "v": 45.0}), b_enter_leave, ("fast",)
        )
    )
    a.append(
        Attack(
            "flash_card",
            "flash",
            _grid(
                f3={"obj": "aadhaar", "frames": 3},
                f6={"obj": "pan", "frames": 6},
                f15={"obj": "aadhaar", "frames": 15},
                f24={"obj": "pan", "frames": 24},
            ),
            b_flash,
            ("f6", "f15"),
            "card on screen for 0.1 / 0.2 / 0.5 / 0.8 s inside a 2 s clip, starting off the detect stride",
        )
    )
    a.append(
        Attack(
            "flash_qr",
            "flash",
            _grid(f6={"obj": "qr", "frames": 6}, f15={"obj": "qr", "frames": 15}),
            b_flash,
            ("f6",),
        )
    )
    fonts_grid = {name: {"font": name} for name in textscenes.available_fonts()}
    fonts_grid["spaced"] = {"font": "spaced"}
    fonts_grid["rotated12"] = {"font": "rotated", "roll": 12.0}
    a.append(
        Attack(
            "unusual_fonts",
            "fonts",
            fonts_grid,
            b_fonts,
            tuple(k for k in ("hershey_script", "ubuntu_thin", "spaced") if k in fonts_grid),
            "a phone number in serif, mono-oblique, thin, condensed, Hershey script, letter-spaced and rotated renders",
        )
    )
    a.append(
        Attack(
            "mixed_script",
            "mixed_script",
            _grid(
                hi_latin_digits={"variant": "hi_latin_digits"},
                hi_deva_digits={"variant": "hi_deva_digits"},
                bn_digits={"variant": "bn_digits"},
                ta_digits={"variant": "ta_digits"},
                mixed_in_number={"variant": "mixed_in_number"},
                hinglish_upi={"variant": "hinglish_upi"},
            ),
            b_mixed_script,
            ("hi_deva_digits", "mixed_in_number"),
        )
    )
    a.append(
        Attack(
            "split_lines",
            "split_lines",
            _grid(
                phone_hyphen={"variant": "phone_hyphen"},
                phone_5_5={"variant": "phone_5_5"},
                upi_at={"variant": "upi_at"},
                email={"variant": "email"},
                aadhaar_3_lines={"variant": "aadhaar_3_lines"},
            ),
            b_split_lines,
            ("phone_hyphen", "upi_at"),
            "one identifier broken across two or three lines; every fragment is a target",
        )
    )
    a.append(
        Attack(
            "separators",
            "separators",
            _grid(
                **{
                    v: {"variant": v}
                    for v in (
                        "hyphen",
                        "dots",
                        "slash",
                        "all_spaced",
                        "parens",
                        "plus91_mixed",
                        "zero_width",
                        "words_en",
                    )
                }
            ),
            b_separators,
            ("all_spaced", "zero_width", "words_en"),
            "zero_width is pixel-identical to the plain number (U+200B draws nothing): it exercises the text path, not the pixels",
        )
    )
    a.append(
        Attack(
            "face_edge",
            "face",
            _grid(edge50={"center": (0.0, 0.5), "w": 0.34}, edge30={"center": (-0.07, 0.5), "w": 0.34}),
            b_face,
            ("edge50",),
            "face half out of the frame; proxy only (no read-back for faces)",
            _face_ready,
        )
    )
    a.append(
        Attack(
            "face_turned",
            "face",
            _grid(yaw50={"yaw": 50.0}, yaw65={"yaw": 65.0}, yaw75={"yaw": 75.0}),
            b_face,
            ("yaw65",),
            "a frontal portrait foreshortened by yaw; NOT a true profile view (no profile source images)",
            _face_ready,
        )
    )
    a.append(
        Attack(
            "face_occluded",
            "face",
            _grid(sunglasses={"accessory": "sunglasses"}, mask={"accessory": "mask"}),
            b_face,
            ("sunglasses", "mask"),
            "synthetic sunglasses bar / face mask drawn over the portrait",
            _face_ready,
        )
    )
    a.append(
        Attack(
            "face_small",
            "face",
            _grid(w10={"w": 0.10}, w06={"w": 0.06}),
            b_face,
            ("w10",),
            "face 40-50 px wide",
            _face_ready,
        )
    )
    a.append(
        Attack(
            "scene_cut_card",
            "scene_cut",
            _grid(every10={"obj": "aadhaar", "period": 10}, every20={"obj": "pan", "period": 20}),
            b_cut,
            ("every10",),
            "hard cut to a new backdrop, card and pose; tags cut+0..cut+5 are the frames right after the cut",
        )
    )
    a.append(
        Attack("scene_cut_qr", "scene_cut", _grid(every10={"obj": "qr", "period": 10}), b_cut, ("every10",))
    )
    a.append(
        Attack(
            "frame_drops",
            "frame_drops",
            _grid(
                slots={"obj": "aadhaar", "pattern": "slots"},
                burst15={"obj": "pan", "pattern": "burst"},
                random30={"obj": "aadhaar", "pattern": "random"},
            ),
            b_drops,
            ("slots", "burst15"),
            "dropped frames never reach the detectors; slots drops exactly the frames the fast lane would run on",
        )
    )
    return {x.name: x for x in a}


CATALOGUE: dict[str, Attack] = _build_catalogue()
GROUPS: tuple[str, ...] = tuple(dict.fromkeys(x.group for x in CATALOGUE.values()))


def skipped(attacks: list[str] | None = None) -> dict[str, str]:
    """Attacks that cannot run in this environment, with the reason (reported as NOT RUN, never as a pass)."""
    out = {}
    for name, at in CATALOGUE.items():
        if attacks and name not in attacks:
            continue
        why = at.requires()
        if why:
            out[name] = why
    return out


def cases(
    seed: int = 1234,
    attacks: list[str] | None = None,
    groups: list[str] | None = None,
    quick: bool = False,
    max_cases: int | None = None,
) -> list[Case]:
    """Reproducible cases: the same (seed, selection) always renders the same frames."""
    out: list[Case] = []
    for name, at in CATALOGUE.items():
        if attacks and name not in attacks:
            continue
        if groups and at.group not in groups:
            continue
        if at.requires():
            continue
        labels = [lb for lb in at.quick if lb in at.grid] if quick else list(at.grid)
        for lb in labels:
            params = at.grid[lb]
            out.append(
                Case(
                    id=f"{name}.{lb}.s{seed}",
                    attack=name,
                    group=at.group,
                    param=lb,
                    params=dict(params),
                    seed=seed,
                    build=(lambda b=at.build, s=seed, lb_=lb, p=params: b(s, lb_, p)),
                    notes=at.note,
                )
            )
    return out[:max_cases] if max_cases else out
