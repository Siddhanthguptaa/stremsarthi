"""SPECIMEN phone screens: OTP notifications, a UPI QR payment screen, and hard-negative screens.

Phones are flat portrait sprites (440x940, 19.5:9 screen) drawn from our own shapes; ``landscape=True`` rotates
the finished sprite 90 degrees (phone held sideways). OTP text uses ``SPECIMEN`` in the sender so the
screens cannot be mistaken for real notifications. Layouts approximate common Android lock screens, banners,
SMS threads and OTP entry screens; they are not copies of any vendor UI.
"""

from __future__ import annotations

import random

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..common import identifiers, specimen, textlayout
from ..common.fonts import load_font
from .compose import Sprite, SpriteTarget, rect_quad
from .qr import qr_image

PW, PH = 440, 940
BEZEL = 16
SCREEN = (BEZEL, BEZEL, PW - BEZEL, PH - BEZEL)

OTP_TEMPLATES: dict[str, list[str]] = {
    "en": [
        "SPECIMEN: your OTP is {code}. Do not share it with anyone.",
        "{code} is your one time password for SPECIMEN BANK. Do not share this code.",
        "Your verification code is {code}. SPECIMEN will never call you to ask for it.",
        "SPECIMEN PAY: use {code} as OTP to confirm your payment. Do not share.",
    ],
    "hinglish": [
        "SPECIMEN: Aapka OTP {code} hai. Ise kisi ke saath share na karein.",
        "Aapka verification code {code} hai. SPECIMEN kabhi OTP nahi maangta.",
    ],
    "hi": [
        "आपका वन टाइम पासवर्ड {code} है। इसे किसी से साझा न करें। - SPECIMEN",
        "आपका ओटीपी {code} है। SPECIMEN बैंक कभी ओटीपी नहीं मांगता।",
    ],
}
APP_TITLES = ("Messages", "SPECIMEN BANK", "SPECIMEN PAY", "SMS")
FILLER_NOTIFICATIONS = (
    ("Calendar", "Stream planning call at 6:30 PM"),
    ("Delivery", "Your order is out for delivery today."),
    ("Weather", "Light rain expected this evening."),
    ("Chat", "New message from Team Alpha"),
    ("Battery", "Battery at 20%. Connect charger."),
    ("Clock", "Alarm: Go live in 15 minutes"),
)
NEG_ORDER_TEXTS = (
    "Order #{n} has been delivered. Rate your experience.",
    "Ticket {n} is confirmed for Saturday.",
    "Your parcel {n} will arrive tomorrow.",
)


def _rounded_alpha(size: tuple[int, int], radius: int) -> np.ndarray:
    a = Image.new("L", size, 0)
    ImageDraw.Draw(a).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return np.asarray(a, dtype=np.uint8)


def _wallpaper(rng: random.Random, w: int, h: int) -> Image.Image:
    c1 = np.array([rng.randint(20, 90), rng.randint(20, 90), rng.randint(60, 150)], np.float32)
    c2 = np.array([rng.randint(60, 160), rng.randint(40, 120), rng.randint(80, 180)], np.float32)
    ramp = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    arr = np.broadcast_to(c1 * (1 - ramp) + c2 * ramp, (h, w, 3))
    return Image.fromarray(arr.astype(np.uint8))


def _status_bar(d: ImageDraw.ImageDraw, x0: int, x1: int, y: int, color=(240, 240, 240)) -> None:
    f = load_font("latin", 20, 600)
    d.text((x0 + 22, y + 8), "12:47", font=f, fill=color)
    d.rounded_rectangle((x1 - 62, y + 10, x1 - 28, y + 26), radius=4, outline=color, width=2)
    d.rectangle((x1 - 58, y + 14, x1 - 42, y + 22), fill=color)
    for i in range(4):
        d.rectangle((x1 - 100 + i * 8, y + 24 - (i + 1) * 4, x1 - 95 + i * 8, y + 26), fill=color)


def _phone_frame() -> tuple[Image.Image, ImageDraw.ImageDraw, tuple[int, int, int, int]]:
    img = Image.new("RGB", (PW, PH), (18, 18, 20))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, PW - 1, PH - 1), radius=58, fill=(22, 22, 25), outline=(70, 70, 78), width=3)
    return img, d, SCREEN


def _finish(img: Image.Image, targets, distractors, context, landscape: bool, meta: dict) -> Sprite:
    arr = np.asarray(img, dtype=np.uint8)
    alpha = _rounded_alpha((PW, PH), 58)
    if not landscape:
        return Sprite(arr, targets, alpha=alpha, distractors=distractors, context=context, meta=meta)
    # rotate 90 degrees clockwise: (x, y) -> (PH - y, x), corner order kept clockwise from top-left
    arr = cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
    alpha = cv2.rotate(alpha, cv2.ROTATE_90_CLOCKWISE)

    def rot(t: SpriteTarget) -> SpriteTarget:
        q = [(PH - y, x) for x, y in t.quad]
        return SpriteTarget(t.category, [q[3], q[0], q[1], q[2]], t.attrs)

    return Sprite(
        arr,
        [rot(t) for t in targets],
        alpha=alpha,
        distractors=[rot(t) for t in distractors],
        context=[rot(t) for t in context],
        meta={**meta, "landscape": True},
    )


def _draw_wrapped(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    size: int,
    max_w: int,
    fill,
    code: str | None,
    weight: int = 400,
) -> tuple[int, tuple[float, float, float, float] | None]:
    """Draw wrapped text; returns (bottom y, box of `code` if present)."""
    box = None
    for line in textlayout.wrap(text, size, max_w, weight):
        base = y + size
        textlayout.draw_line(d, x, base, line, size, fill, weight)
        if code and box is None:
            box = textlayout.substring_box(line, code, x, base, size, weight)
        y += int(size * 1.35)
    return y, box


def render_otp_screen(rng: random.Random, landscape: bool = False, layout: str | None = None) -> Sprite:
    """Phone showing an OTP in a notification banner, SMS bubble or OTP entry screen."""
    layout = layout or rng.choice(("lock", "banner", "sms", "entry"))
    code = identifiers.otp_code(rng)
    lang = rng.choices(("en", "hinglish", "hi"), weights=(5, 2, 3))[0]
    text = rng.choice(OTP_TEMPLATES[lang]).format(code=code)
    app = rng.choice(APP_TITLES)
    img, d, (sx1, sy1, sx2, sy2) = _phone_frame()
    img.paste(_wallpaper(rng, sx2 - sx1, sy2 - sy1), (sx1, sy1))
    d = ImageDraw.Draw(img)
    _status_bar(d, sx1, sx2, sy1 + 6)
    code_box = None
    banner_box: tuple[float, float, float, float] = (sx1 + 14, 0, sx2 - 14, 0)
    if layout == "lock":
        d.text(
            ((sx1 + sx2) / 2, sy1 + 150),
            "12:47",
            font=load_font("latin", 92, 300),
            fill=(255, 255, 255),
            anchor="mm",
        )
        d.text(
            ((sx1 + sx2) / 2, sy1 + 215),
            "Fri, 20 Nov",
            font=load_font("latin", 24),
            fill=(235, 235, 240),
            anchor="mm",
        )
        notes = [(app, text)] + [rng.choice(FILLER_NOTIFICATIONS) for _ in range(rng.randint(0, 2))]
        rng.shuffle(notes)
        y = sy1 + 280
        for title, body in notes:
            is_otp = body == text
            lines = textlayout.wrap(body, 21, sx2 - sx1 - 56)
            bh = 54 + len(lines) * 30
            panel = Image.new("RGBA", (sx2 - sx1 - 28, bh), (245, 245, 248, 235))
            img.paste(panel, (sx1 + 14, y), panel)
            d = ImageDraw.Draw(img)
            d.text((sx1 + 30, y + 10), title, font=load_font("latin", 18, 700), fill=(60, 60, 70))
            _, cb = _draw_wrapped(
                d, sx1 + 30, y + 34, body, 21, sx2 - sx1 - 56, (20, 20, 25), code if is_otp else None
            )
            if is_otp:
                banner_box = (sx1 + 14, y, sx2 - 14, y + bh)
                code_box = cb
            y += bh + 12
    elif layout == "banner":
        for r in range(5):  # home-screen icon grid
            for c in range(4):
                x, yy = sx1 + 30 + c * 92, sy1 + 300 + r * 110
                d.rounded_rectangle(
                    (x, yy, x + 62, yy + 62),
                    radius=16,
                    fill=(rng.randint(60, 230), rng.randint(60, 230), rng.randint(60, 230)),
                )
        lines = textlayout.wrap(text, 21, sx2 - sx1 - 64)
        bh = 64 + len(lines) * 30
        by = sy1 + 60
        panel = Image.new("RGBA", (sx2 - sx1 - 24, bh), (250, 250, 252, 245))
        img.paste(panel, (sx1 + 12, by), panel)
        d = ImageDraw.Draw(img)
        d.text((sx1 + 30, by + 12), f"{app}  -  now", font=load_font("latin", 18, 700), fill=(60, 60, 70))
        _, code_box = _draw_wrapped(d, sx1 + 30, by + 38, text, 21, sx2 - sx1 - 64, (20, 20, 25), code)
        banner_box = (sx1 + 12, by, sx2 - 12, by + bh)
    elif layout == "sms":
        d.rectangle((sx1, sy1 + 36, sx2, sy1 + 100), fill=(250, 250, 252))
        d.text((sx1 + 24, sy1 + 52), app, font=load_font("latin", 26, 700), fill=(30, 30, 40))
        d.rectangle((sx1, sy1 + 100, sx2, sy2), fill=(236, 238, 242))
        y = sy1 + 130
        for _ in range(rng.randint(1, 3)):
            _t, b = rng.choice(FILLER_NOTIFICATIONS)
            lines = textlayout.wrap(b, 21, sx2 - sx1 - 120)
            bh = 30 + len(lines) * 30
            d.rounded_rectangle((sx1 + 16, y, sx2 - 70, y + bh), radius=18, fill=(255, 255, 255))
            _draw_wrapped(d, sx1 + 32, y + 8, b, 21, sx2 - sx1 - 120, (30, 30, 40), None)
            y += bh + 14
        lines = textlayout.wrap(text, 22, sx2 - sx1 - 120)
        bh = 30 + len(lines) * 32
        d.rounded_rectangle((sx1 + 16, y, sx2 - 40, y + bh), radius=18, fill=(255, 255, 255))
        _, code_box = _draw_wrapped(d, sx1 + 32, y + 8, text, 22, sx2 - sx1 - 100, (20, 20, 25), code)
        banner_box = (sx1 + 16, y, sx2 - 40, y + bh)
    else:  # entry: in-app OTP boxes
        d.rectangle((sx1, sy1 + 36, sx2, sy2), fill=(250, 250, 252))
        d.text(
            ((sx1 + sx2) / 2, sy1 + 120),
            "Verification code",
            font=load_font("latin", 32, 700),
            fill=(25, 30, 60),
            anchor="mm",
        )
        _draw_wrapped(
            d, sx1 + 30, sy1 + 160, "Enter the one time password sent by SPECIMEN to confirm. Do not share it.", 20,
            sx2 - sx1 - 60, (90, 90, 100), None,
        )  # fmt: skip
        n = len(code)
        bw = min(58, (sx2 - sx1 - 60) // n - 8)
        total = n * (bw + 8) - 8
        x0 = (sx1 + sx2 - total) // 2
        yb = sy1 + 300
        f = load_font("latin", 36, 700)
        for i, ch in enumerate(code):
            x = x0 + i * (bw + 8)
            d.rounded_rectangle(
                (x, yb, x + bw, yb + 74), radius=10, outline=(70, 90, 190), width=3, fill=(255, 255, 255)
            )
            d.text((x + bw / 2, yb + 37), ch, font=f, fill=(10, 10, 20), anchor="mm")
        code_box = (x0, yb, x0 + total, yb + 74)
        banner_box = (sx1 + 10, sy1 + 100, sx2 - 10, yb + 100)
        d.rounded_rectangle((sx1 + 60, yb + 130, sx2 - 60, yb + 190), radius=30, fill=(70, 90, 190))
        d.text(
            ((sx1 + sx2) / 2, yb + 160),
            "VERIFY",
            font=load_font("latin", 26, 700),
            fill=(255, 255, 255),
            anchor="mm",
        )
    d.text(
        ((sx1 + sx2) / 2, sy2 - 18),
        "SPECIMEN",
        font=load_font("latin", 20, 700),
        fill=(255, 120, 120),
        anchor="mm",
    )
    pad = 3
    targets = [
        SpriteTarget(
            "otp_banner", rect_quad(banner_box), {"layout": layout, "lang": lang, "code_digits": len(code)}
        )
    ]
    if code_box:
        cx1, cy1, cx2, cy2 = code_box
        targets.append(
            SpriteTarget(
                "otp_code",
                rect_quad((cx1 - pad, cy1 - pad, cx2 + pad, cy2 + pad)),
                {"code": code, "lang": lang},
            )
        )
    context = [SpriteTarget("phone_screen", rect_quad(SCREEN), {})]
    return _finish(img, targets, [], context, landscape, {"layout": layout, "lang": lang, "code": code})


def render_negative_screen(rng: random.Random, landscape: bool = False) -> Sprite:
    """Phone screens without any OTP: icon grid, chat, text-heavy page, delivery notice with a number."""
    kind = rng.choice(("grid", "chat", "page", "order"))
    img, d, (sx1, sy1, sx2, sy2) = _phone_frame()
    img.paste(_wallpaper(rng, sx2 - sx1, sy2 - sy1), (sx1, sy1))
    d = ImageDraw.Draw(img)
    _status_bar(d, sx1, sx2, sy1 + 6)
    distractors: list[SpriteTarget] = []
    if kind == "grid":
        names = ("Maps", "Clock", "Photos", "Notes", "Music", "Camera", "Play", "Chat")
        for r in range(6):
            for c in range(4):
                x, yy = sx1 + 30 + c * 92, sy1 + 90 + r * 120
                d.rounded_rectangle(
                    (x, yy, x + 62, yy + 62),
                    radius=16,
                    fill=(rng.randint(60, 230), rng.randint(60, 230), rng.randint(60, 230)),
                )
                d.text(
                    (x + 31, yy + 84),
                    rng.choice(names),
                    font=load_font("latin", 16),
                    fill=(245, 245, 245),
                    anchor="mm",
                )
    elif kind == "chat":
        d.rectangle((sx1, sy1 + 36, sx2, sy2), fill=(236, 238, 242))
        y = sy1 + 70
        for _ in range(5):
            _t, b = rng.choice(FILLER_NOTIFICATIONS)
            lines = textlayout.wrap(b, 21, sx2 - sx1 - 120)
            bh = 30 + len(lines) * 30
            d.rounded_rectangle((sx1 + 16, y, sx2 - 70, y + bh), radius=18, fill=(255, 255, 255))
            _draw_wrapped(d, sx1 + 32, y + 8, b, 21, sx2 - sx1 - 120, (30, 30, 40), None)
            y += bh + 14
    elif kind == "page":
        d.rectangle((sx1, sy1 + 36, sx2, sy2), fill=(252, 252, 252))
        y = sy1 + 60
        sample = (
            "Stream tips for new creators. Keep your camera away from documents and screens. "
            "Check your overlay before going live. Use a clean background and good lighting."
        )
        for _ in range(3):
            y, _b = _draw_wrapped(d, sx1 + 24, y, sample, 20, sx2 - sx1 - 48, (40, 40, 50), None)
            y += 16
    else:
        n = "".join(rng.choice("0123456789") for _ in range(6))
        body = rng.choice(NEG_ORDER_TEXTS).format(n=n)
        lines = textlayout.wrap(body, 21, sx2 - sx1 - 56)
        bh = 54 + len(lines) * 30
        y = sy1 + 280
        panel = Image.new("RGBA", (sx2 - sx1 - 28, bh), (245, 245, 248, 235))
        img.paste(panel, (sx1 + 14, y), panel)
        d = ImageDraw.Draw(img)
        d.text((sx1 + 30, y + 10), "Delivery", font=load_font("latin", 18, 700), fill=(60, 60, 70))
        _, box = _draw_wrapped(d, sx1 + 30, y + 34, body, 21, sx2 - sx1 - 56, (20, 20, 25), n)
        if box:
            distractors.append(SpriteTarget("six_digit_non_otp", rect_quad(box), {"digits": len(n)}))
    d.text(
        ((sx1 + sx2) / 2, sy2 - 18),
        "SPECIMEN",
        font=load_font("latin", 20, 700),
        fill=(255, 120, 120),
        anchor="mm",
    )
    distractors.append(SpriteTarget("non_otp_screen", rect_quad(SCREEN), {"layout": kind}))
    return _finish(
        img,
        [],
        distractors,
        [SpriteTarget("phone_screen", rect_quad(SCREEN), {})],
        landscape,
        {"layout": kind},
    )


def render_qr_phone(rng: random.Random, payload: str | None = None, landscape: bool = False) -> Sprite:
    """Phone payment screen showing a UPI QR (positive) or another QR (negative) plus a SPECIMEN merchant label."""
    upi = None
    if payload is None:
        upi = specimen.upi_id(rng, rng.choice(specimen.UPI_HANDLES))
        payload = specimen.upi_payload(upi)
    positive = payload.startswith("upi://")
    img, d, (sx1, sy1, sx2, sy2) = _phone_frame()
    d.rectangle((sx1, sy1, sx2, sy2), fill=(250, 250, 253))
    d.rectangle((sx1, sy1, sx2, sy1 + 110), fill=(60, 80, 160))
    _status_bar(d, sx1, sx2, sy1 + 6)
    d.text(
        ((sx1 + sx2) / 2, sy1 + 70),
        "Scan to pay",
        font=load_font("latin", 30, 700),
        fill=(255, 255, 255),
        anchor="mm",
    )
    box = 330
    qx, qy = (sx1 + sx2 - box) // 2, sy1 + 170
    img.paste(qr_image(payload, box), (qx, qy))
    d = ImageDraw.Draw(img)
    d.text(
        ((sx1 + sx2) / 2, qy + box + 50),
        "SPECIMEN STORE",
        font=load_font("latin", 26, 700),
        fill=(30, 30, 50),
        anchor="mm",
    )
    if upi:
        d.text(
            ((sx1 + sx2) / 2, qy + box + 100),
            upi,
            font=load_font("latin", 20),
            fill=(60, 60, 80),
            anchor="mm",
        )
    d.text(
        ((sx1 + sx2) / 2, sy2 - 18),
        "SPECIMEN",
        font=load_font("latin", 20, 700),
        fill=(200, 60, 60),
        anchor="mm",
    )
    qr_t = SpriteTarget(
        "upi_qr", rect_quad((qx, qy, qx + box, qy + box)), {"payload": payload, "upi_id": upi}
    )
    targets: list[SpriteTarget] = []
    dist: list[SpriteTarget] = []
    if positive:
        targets.append(qr_t)
    else:
        dist.append(SpriteTarget("non_upi_qr", qr_t.quad, {"payload": payload}))
    return _finish(
        img,
        targets,
        dist,
        [SpriteTarget("phone_screen", rect_quad(SCREEN), {})],
        landscape,
        {"positive": positive},
    )
