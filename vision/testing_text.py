"""SPECIMEN text scenes for the ``text`` detector: PII lines in five scripts, signboards, hard negatives.

Everything rendered here is synthetic: fake phone numbers, PIN codes, UPI IDs and e-mails (no real person,
no real number) on notes, phone-like panels and signboards, with a small ``SPECIMEN`` tag in the corner of
every image. ``eval`` is only used for the desk backdrops (imported lazily) and the stamp exclusion zone.

Fonts: Latin from ``engine/assets`` (Atkinson Hyperlegible Next, OFL) or DejaVu; Indic scripts are searched in
``eval/fonts`` (T2.1) and ``.tmp/fonts`` (Noto Sans from the google/fonts repo, see
``vision/tests/fetch_text_fonts.py``). ``find_font(script)`` returns None when a font is missing and the tests
skip instead of failing.

Each ``TextScene`` carries ground truth: ``items`` (category, quad of the sensitive substring, script) and
``lines`` (every text line drawn, for the signboard recall). ``score_text_scene`` rasterises the detections.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

from contracts import Category, Detection

REPO = Path(__file__).resolve().parents[1]
CANVAS_W, CANVAS_H = 1280, 720
SPECIMEN_TAG = "SPECIMEN"

_FONT_GLOBS: dict[str, tuple[str, ...]] = {
    "latin": ("AtkinsonHyperlegibleNext*.ttf", "*NotoSans-*.ttf", "DejaVuSans.ttf"),
    "devanagari": ("*NotoSansDevanagari*.ttf",),
    "tamil": ("*NotoSansTamil*.ttf",),
    "telugu": ("*NotoSansTelugu*.ttf",),
    "kannada": ("*NotoSansKannada*.ttf",),
    "bengali": ("*NotoSansBengali*.ttf",),
}
_FONT_DIRS = (
    REPO / "eval" / "fonts",
    REPO / ".tmp" / "fonts",
    REPO / "engine" / "assets",
    Path("/usr/share/fonts"),
)

_DIGITS = {
    "latin": "0123456789",
    "devanagari": "०१२३४५६७८९",
    "tamil": "௦௧௨௩௪௫௬௭௮௯",
    "telugu": "౦౧౨౩౪౫౬౭౮౯",
    "kannada": "೦೧೨೩೪೫೬೭೮೯",
    "bengali": "০১২৩৪৫৬৭৮৯",
}

# words per script for signboards and filler lines (SPECIMEN shop names, nothing real)
SIGN_TEXT: dict[str, list[str]] = {
    "devanagari": ["श्री गणेश किराना स्टोर", "नया बाज़ार रोड", "शर्मा मिष्ठान भंडार", "सरकारी अस्पताल"],
    "kannada": ["ಶ್ರೀ ಲಕ್ಷ್ಮಿ ಸ್ಟೋರ್ಸ್", "ಹೊಸ ಬಜಾರ್ ರಸ್ತೆ", "ಬೆಂಗಳೂರು ಬೇಕರಿ", "ಸರ್ಕಾರಿ ಆಸ್ಪತ್ರೆ"],
    "tamil": ["ஸ்ரீ முருகன் ஸ்டோர்ஸ்", "புதிய பஜார் சாலை", "சென்னை பேக்கரி", "அரசு மருத்துவமனை"],
    "telugu": ["శ్రీ వెంకటేశ్వర స్టోర్స్", "కొత్త బజార్ రోడ్", "హైదరాబాద్ బేకరీ", "ప్రభుత్వ ఆసుపత్రి"],
    "bengali": ["শ্রী দুর্গা মিষ্টান্ন ভাণ্ডার", "নতুন বাজার রোড", "কলকাতা বেকারি", "সরকারি হাসপাতাল"],
    "latin": ["SHREE GANESH STORES", "NEW MARKET ROAD", "SHARMA SWEETS", "CITY BAKERY"],
}
_LABELS = {  # label words that sit before a number (phone / PIN) in each script
    "devanagari": {"phone": ["फोन: ", "मोबाइल "], "pin": ["पिन कोड: ", "पिनकोड - "]},
    "tamil": {"phone": ["தொலைபேசி: ", "அழைக்க "], "pin": ["பின்கோடு: ", "பின் - "]},
    "telugu": {"phone": ["ఫోన్: ", "కాల్ చేయండి "], "pin": ["పిన్ కోడ్: ", "పిన్ - "]},
    "latin": {
        "phone": ["Call: ", "WhatsApp ", "Ph: ", "Mob. "],
        "pin": ["PIN: ", "Pincode - ", "Pin code: "],
    },
}
UPI_HANDLES = ("okhdfcbank", "oksbi", "ybl", "paytm", "okaxis", "okicici", "ibl", "axl")
_NAMES = ("rahul", "priya.s", "amit_k", "sneha99", "vikram.r", "anita-m", "dev.patel", "kiran07")
_FILLER = (
    "Order placed successfully",
    "Thank you for shopping",
    "Total amount 1,299",
    "Delivery in 3 days",
    "Version 2.4.1 build 880",
    "Offer ends 12/03/2026",
)


def find_font(script: str) -> Path | None:
    """First font file for ``script`` in the search dirs (None when absent: tests then skip)."""
    for pattern in _FONT_GLOBS[script]:
        for base in _FONT_DIRS:
            if base.is_dir():
                found = sorted(base.rglob(pattern))
                if found:
                    return found[0]
    return None


def fonts_available(*scripts: str) -> bool:
    return all(find_font(s) is not None for s in scripts)


def _font(script: str, size: int) -> ImageFont.FreeTypeFont:
    path = find_font(script)
    if path is None:
        raise FileNotFoundError(f"no font for {script}")
    engine = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(path), size, layout_engine=engine)


def native_digits(digits: str, script: str) -> str:
    table = str.maketrans("0123456789", _DIGITS[script])
    return digits.translate(table)


# ---------------------------------------------------------------------------- scene model
@dataclass
class Item:
    category: Category
    quad: np.ndarray  # (4, 2) float32 in the frame
    script: str
    digits: str = "ascii"  # "native" when the number is written in the script's own digits


@dataclass
class TextScene:
    image: np.ndarray  # BGR uint8 720x1280x3
    kind: str
    items: list[Item] = field(default_factory=list)  # sensitive substrings (PII ground truth)
    lines: list[np.ndarray] = field(default_factory=list)  # every drawn text line (signboard truth), (4, 2)
    line_texts: list[str] = field(default_factory=list)  # SPECIMEN text of each line (fake OCR only)
    line_scripts: list[str] = field(default_factory=list)
    ignore: list[np.ndarray] = field(default_factory=list)  # the SPECIMEN tag: neither truth nor a false mask
    params: dict[str, object] = field(default_factory=dict)

    def categories(self) -> set[Category]:
        return {i.category for i in self.items}


@dataclass(frozen=True)
class Segment:
    text: str
    category: Category | None = None
    digits: str = "ascii"


def _phone_digits(rng: random.Random) -> tuple[str, str]:
    first = rng.choice("6789")
    a = first + "".join(rng.choice("0123456789") for _ in range(4))
    b = "".join(rng.choice("0123456789") for _ in range(5))
    return a, b


def _pin_digits(rng: random.Random) -> str:
    circle = rng.choice([11, 20, 30, 40, 50, 56, 60, 70, 80, 41, 62, 38, 68])
    return f"{circle}{rng.randint(0, 9999):04d}"


def pii_line(rng: random.Random, category: Category, script: str) -> tuple[list[Segment], str]:
    """Segments of one SPECIMEN line holding ``category`` in ``script`` (digits native or Latin at random)."""
    label_script = script if script in _LABELS else "latin"
    # Devanagari numerals are common and readable; Tamil/Telugu numerals are rare in modern use (and the PP-OCR
    # mobile models read them badly), so they are a small share of the test lines and reported on their own.
    native = (script == "devanagari" and rng.random() < 0.65) or (
        script in ("tamil", "telugu") and rng.random() < 0.2
    )
    digits = "native" if native else "ascii"
    if category is Category.PHONE_NUMBER:
        a, b = _phone_digits(rng)
        number = rng.choice([f"{a} {b}", f"{a}{b}", f"{a}-{b}", f"+91 {a} {b}", f"+91-{a}{b}", f"0{a} {b}"])
        if native:
            number = native_digits(number, script)
        return [
            Segment(rng.choice(_LABELS[label_script]["phone"])),
            Segment(number, category, digits),
        ], label_script
    if category is Category.PIN_CODE:
        pin = _pin_digits(rng)
        number = rng.choice([pin, f"{pin[:3]} {pin[3:]}"])
        if native:
            number = native_digits(number, script)
        if script == "latin" and rng.random() < 0.4:
            head = f"{rng.randint(1, 99)}, MG Road, Bengaluru - "
            return [Segment(head), Segment(number, category, digits)], "latin"
        return [
            Segment(rng.choice(_LABELS[label_script]["pin"])),
            Segment(number, category, digits),
        ], label_script
    if category is Category.UPI_ID:
        upi = f"{rng.choice(_NAMES)}@{rng.choice(UPI_HANDLES)}"
        if rng.random() < 0.3:
            upi = f"{rng.choice(['98', '97', '70', '63'])}{rng.randint(10**7, 10**8 - 1)}@{rng.choice(UPI_HANDLES)}"
        return [Segment(rng.choice(["Pay: ", "UPI ID: ", "UPI: "])), Segment(upi, category)], "latin"
    if category is Category.EMAIL:
        mail = f"{rng.choice(_NAMES).replace('-', '.')}@{rng.choice(['gmail.com', 'outlook.com', 'yahoo.in', 'example.co.in'])}"
        return [Segment(rng.choice(["Mail: ", "Email: ", "Contact "])), Segment(mail, category)], "latin"
    raise ValueError(f"no PII line for {category}")


# hard negatives: strings that look like PII but are not (all Latin, plus Devanagari filler)
NEGATIVE_LINES: tuple[str, ...] = (
    "Order #AB123456 confirmed",
    "Invoice no 1234567890",  # 10 digits starting 1: not a mobile number
    "Total price Rs 12,99,999 only",
    "Date 12/03/2026 time 10:45",
    "Version 2.4.1 build 880 beta",
    "Follow @streamer_name on social",
    "Visit www.example.com/home now",
    "Server 192.168.1.1 ready",
    "KA01AB1234 parked outside",
    "Reference 000123 closed",
    "Player level 87 rank 4321",
    "The total amount paid for the whole order was 560001 rupees in the end today",
    "Frame 2048 of 4096 exported",
    "Call centre 1800 123 456",  # toll free: not a 6-9 mobile
    "Temperature 36.5 C humidity 70",
)


# ---------------------------------------------------------------------------- rendering
def _surface(rng: random.Random, kind: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(background, text) colours (RGB) of the surface the text sits on."""
    if kind == "sign":
        return rng.choice(
            [
                ((0, 110, 60), (255, 255, 255)),
                ((20, 60, 150), (255, 255, 255)),
                ((250, 205, 40), (20, 20, 20)),
                ((245, 245, 245), (190, 20, 20)),
                ((200, 30, 30), (255, 255, 255)),
            ]
        )
    return rng.choice(
        [
            ((246, 246, 240), (25, 25, 30)),
            ((255, 255, 255), (10, 10, 10)),
            ((28, 30, 38), (235, 235, 235)),
            ((240, 232, 200), (40, 30, 20)),
        ]
    )


def _backdrop(seed: int, name: str) -> np.ndarray:
    from eval.data_gen.video.dataset import backdrop

    return cv2.cvtColor(backdrop(seed, name), cv2.COLOR_RGB2BGR)


def render_lines(
    rng: random.Random,
    lines: list[tuple[list[Segment], str]],
    *,
    kind: str,
    size_range: tuple[int, int],
    tilt: float = 0.0,
) -> tuple[Image.Image, list[Item], list[np.ndarray], list[str], list[str]]:
    """Draw lines on a surface sprite. Returns (RGBA sprite, items, line quads, line texts, line scripts),
    all in sprite coordinates."""
    bg, fg = _surface(rng, kind)
    size = rng.randint(*size_range)
    fonts = {s: _font(s, size) for _, s in lines}
    meas = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    margin = int(size * 0.7)
    gap = int(size * 0.55)
    widths = [meas.textlength("".join(s.text for s in segs), font=fonts[sc]) for segs, sc in lines]
    heights = [
        fonts[sc].getbbox("Hgpq")[3] - fonts[sc].getbbox("Hgpq")[1] + int(size * 0.7) for _, sc in lines
    ]
    width = int(max(widths) + 2 * margin)
    height = int(sum(heights) + gap * (len(lines) - 1) + 2 * margin)
    sprite = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(sprite)
    items: list[Item] = []
    quads: list[np.ndarray] = []
    texts: list[str] = []
    scripts: list[str] = []
    y = margin
    for (segs, script), line_h in zip(lines, heights, strict=True):
        font = fonts[script]
        full = "".join(s.text for s in segs)
        draw.text((margin, y), full, font=font, fill=fg)
        top = y + 0.1 * size
        x0 = float(margin)
        quads.append(
            np.array(
                [
                    [x0 - 2, top],
                    [x0 + meas.textlength(full, font=font) + 2, top],
                    [x0 + meas.textlength(full, font=font) + 2, top + line_h - 0.5 * size],
                    [x0 - 2, top + line_h - 0.5 * size],
                ],
                np.float32,
            )
        )
        texts.append(full)
        scripts.append(script)
        prefix = ""
        for seg in segs:
            start = x0 + meas.textlength(prefix, font=font)
            end = x0 + meas.textlength(prefix + seg.text, font=font)
            if seg.category is not None:
                items.append(
                    Item(
                        seg.category,
                        np.array(
                            [
                                [start, top],
                                [end, top],
                                [end, top + line_h - 0.5 * size],
                                [start, top + line_h - 0.5 * size],
                            ],
                            np.float32,
                        ),
                        script,
                        seg.digits,
                    )
                )
            prefix += seg.text
        y += line_h + gap
    return sprite.convert("RGBA"), items, quads, texts, scripts


def compose(
    rng: random.Random,
    sprite: Image.Image,
    items: list[Item],
    quads: list[np.ndarray],
    backdrop: np.ndarray,
    *,
    tilt: float,
    scale: float = 1.0,
) -> tuple[np.ndarray, list[Item], list[np.ndarray]]:
    """Place ``sprite`` on ``backdrop`` (rotation ``tilt`` degrees, scale), carrying the truth quads along."""
    from eval.sender.stamp import STAMP_EXCLUSION

    sw, sh = sprite.size
    scale = min(scale, 0.9 * CANVAS_W / sw, 0.85 * CANVAS_H / sh)  # a long line shrinks to fit the frame
    arr = cv2.cvtColor(np.asarray(sprite.convert("RGB")), cv2.COLOR_RGB2BGR)
    for _ in range(200):
        cx, cy = rng.uniform(0.3, 0.7) * CANVAS_W, rng.uniform(0.35, 0.65) * CANVAS_H
        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), tilt, scale)
        m[0, 2] += cx - sw / 2
        m[1, 2] += cy - sh / 2
        corners = np.array([[0, 0, 1], [sw, 0, 1], [sw, sh, 1], [0, sh, 1]], np.float64)
        box = (m @ corners.T).T
        x1, y1, x2, y2 = STAMP_EXCLUSION
        inside = (
            box[:, 0].min() >= 8
            and box[:, 1].min() >= 8
            and box[:, 0].max() <= CANVAS_W - 8
            and box[:, 1].max() <= CANVAS_H - 8
        )
        stamp = not (
            box[:, 0].max() < x1 or box[:, 0].min() > x2 or box[:, 1].max() < y1 or box[:, 1].min() > y2
        )
        if inside and not stamp:
            break
    else:
        raise RuntimeError("could not place the sprite inside the frame")
    warped = cv2.warpAffine(
        arr, m, (CANVAS_W, CANVAS_H), flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    )
    mask = cv2.warpAffine(np.full((sh, sw), 255, np.uint8), m, (CANVAS_W, CANVAS_H))
    alpha = (mask.astype(np.float32) / 255.0)[..., None]
    img = (backdrop.astype(np.float32) * (1 - alpha) + warped.astype(np.float32) * alpha).astype(np.uint8)

    def tf(q: np.ndarray) -> np.ndarray:
        pts = np.hstack([q, np.ones((4, 1), np.float32)])
        return (m @ pts.T).T.astype(np.float32)

    return img, [Item(i.category, tf(i.quad), i.script, i.digits) for i in items], [tf(q) for q in quads]


TAG_QUAD = np.array(
    [[12, CANVAS_H - 36], [118, CANVAS_H - 36], [118, CANVAS_H - 8], [12, CANVAS_H - 8]], np.float32
)


def _tag(img: np.ndarray) -> None:
    cv2.putText(
        img, SPECIMEN_TAG, (16, CANVAS_H - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (210, 210, 210), 1, cv2.LINE_AA
    )


def _finish(
    img: np.ndarray, rng: random.Random, *, blur: float, noise: float, brightness: float
) -> np.ndarray:
    out = img
    if blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), blur)
    out = np.clip(out.astype(np.float32) * brightness, 0, 255)
    if noise > 0:
        out = out + np.random.default_rng(rng.randint(0, 2**31)).normal(0, noise, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


PII_SCRIPTS = ("latin", "devanagari", "tamil", "telugu")
PII_CATEGORIES = (Category.PHONE_NUMBER, Category.PIN_CODE, Category.UPI_ID, Category.EMAIL)


def make_pii_scene(
    index: int,
    seed: int = 1,
    *,
    tilt: float | None = None,
    blur: float = 0.0,
    brightness: float = 1.0,
    noise: float = 3.0,
    scale: float = 1.0,
) -> TextScene:
    """A note or panel with one or two PII lines (cycling categories and scripts) plus filler lines."""
    rng = random.Random(f"pii-{seed}-{index}")
    category = PII_CATEGORIES[index % 4]
    script = "latin" if category in (Category.UPI_ID, Category.EMAIL) else PII_SCRIPTS[(index // 4) % 4]
    if script != "latin" and not fonts_available(script):
        script = "latin"
    lines = [pii_line(rng, category, script)]
    if rng.random() < 0.5:  # a second PII line of another category on the same note
        other = rng.choice([c for c in PII_CATEGORIES if c is not category])
        lines.append(pii_line(rng, other, "latin"))
    for _ in range(rng.randint(1, 2)):
        lines.append(([Segment(rng.choice(_FILLER))], "latin"))
    rng.shuffle(lines)
    tilt = rng.uniform(-6, 6) if tilt is None else tilt
    sprite, items, quads, texts, scripts = render_lines(rng, lines, kind="note", size_range=(30, 42))
    img, items, quads = compose(
        rng, sprite, items, quads, _backdrop(seed, f"pii{index}"), tilt=tilt, scale=scale
    )
    _tag(img)
    img = _finish(img, rng, blur=blur, noise=noise, brightness=brightness)
    return TextScene(
        img, "pii", items, quads, texts, scripts, [TAG_QUAD], {"tilt": round(tilt, 1), "script": script}
    )


SIGN_SCRIPTS = ("devanagari", "kannada", "tamil", "telugu", "bengali")


def make_sign_scene(
    index: int,
    seed: int = 1,
    *,
    tilt: float | None = None,
    blur: float = 0.0,
    brightness: float = 1.0,
    noise: float = 3.0,
    scale: float = 1.0,
) -> TextScene:
    """A signboard with two or three lines in one Indic script (cycling the five scripts)."""
    rng = random.Random(f"sign-{seed}-{index}")
    script = SIGN_SCRIPTS[index % 5]
    if not fonts_available(script):
        script = "latin"
    words = SIGN_TEXT[script][:]
    rng.shuffle(words)
    lines = [([Segment(w)], script) for w in words[: rng.randint(2, 3)]]
    tilt = rng.uniform(-6, 6) if tilt is None else tilt
    sprite, items, quads, texts, scripts = render_lines(rng, lines, kind="sign", size_range=(40, 62))
    img, items, quads = compose(
        rng, sprite, items, quads, _backdrop(seed, f"sign{index}"), tilt=tilt, scale=scale
    )
    _tag(img)
    img = _finish(img, rng, blur=blur, noise=noise, brightness=brightness)
    return TextScene(
        img, "sign", [], quads, texts, scripts, [TAG_QUAD], {"tilt": round(tilt, 1), "script": script}
    )


def make_negative_scene(index: int, seed: int = 1) -> TextScene:
    """Hard negatives: PII-lookalikes on a note, or an empty desk (``index`` % 5 == 4)."""
    rng = random.Random(f"neg-{seed}-{index}")
    if index % 5 == 4:
        img = _backdrop(seed, f"neg{index}")
        _tag(img)
        return TextScene(img, "negative", ignore=[TAG_QUAD], params={"empty": True})
    chosen = rng.sample(NEGATIVE_LINES, rng.randint(2, 3))
    lines = [([Segment(t)], "latin") for t in chosen]
    sprite, items, quads, texts, scripts = render_lines(rng, lines, kind="note", size_range=(30, 40))
    img, items, quads = compose(
        rng, sprite, items, quads, _backdrop(seed, f"neg{index}"), tilt=rng.uniform(-4, 4)
    )
    _tag(img)
    img = _finish(img, rng, blur=0.0, noise=3.0, brightness=1.0)
    return TextScene(img, "negative", [], quads, texts, scripts, [TAG_QUAD])


# ---------------------------------------------------------------------------- scoring
def _mask(quads: list[np.ndarray]) -> np.ndarray:
    m = np.zeros((CANVAS_H, CANVAS_W), np.uint8)
    for q in quads:
        cv2.fillConvexPoly(m, np.round(q).astype(np.int32), 255)
    return m


def coverage(truth: np.ndarray, quads: list[np.ndarray]) -> float:
    """Share of the ``truth`` quad's area covered by the union of ``quads``."""
    t = _mask([truth])
    area = int((t > 0).sum())
    if area == 0:
        return 0.0
    return float(((t > 0) & (_mask(quads) > 0)).sum() / area)


def det_quads(dets: list[Detection], category: Category | None = None) -> list[np.ndarray]:
    return [np.array(d.quad.pts, np.float32) for d in dets if category is None or d.category is category]


def is_true_pii(scene: TextScene, d: Detection) -> bool:
    """A PII detection is right when its box covers >= 50 % of a same-category truth item or lies >= 30 %
    inside one (a whole-line box around a number counts)."""
    q = np.array(d.quad.pts, np.float32)
    return any(
        coverage(i.quad, [q]) >= 0.5 or coverage(q, [i.quad]) >= 0.3
        for i in scene.items
        if i.category is d.category
    )


@dataclass
class SceneOutcome:
    kind: str
    item_hit: list[bool]  # per truth item: covered >= 0.7 by a same-category detection
    item_masked: list[bool]  # per truth item: covered >= 0.7 by any mask (any category, incl. signboard)
    line_covered: list[bool]  # per text line: covered >= 0.7 by any mask (signboard or PII)
    false_pii: int  # PII-category detections that overlap no truth item of their category
    false_sign: int  # signboard detections overlapping no drawn text line
    detections: int


def score_text_scene(scene: TextScene, dets: list[Detection], threshold: float = 0.7) -> SceneOutcome:
    all_masks = det_quads(dets)
    item_hit = [coverage(i.quad, det_quads(dets, i.category)) >= threshold for i in scene.items]
    item_masked = [coverage(i.quad, all_masks) >= threshold for i in scene.items]
    line_covered = [coverage(q, all_masks) >= threshold for q in scene.lines]
    false_pii = sum(1 for d in dets if d.category is not Category.SIGNBOARD and not is_true_pii(scene, d))
    known = [*scene.lines, *scene.ignore]
    false_sign = sum(
        1
        for d in dets
        if d.category is Category.SIGNBOARD
        and not any(coverage(np.array(d.quad.pts, np.float32), [t]) >= 0.3 for t in known)
    )
    return SceneOutcome(scene.kind, item_hit, item_masked, line_covered, false_pii, false_sign, len(dets))


def scene_fake_ocr(scene: TextScene, *, conf: float = 0.97, digits_ascii: bool = True):
    """A fake ``OcrProvider`` that returns the true lines (one box per line) of ``scene``: for logic tests."""
    from contracts import OcrResult, Quad
    from vision.ocr.normalize import normalise_text

    boxes = tuple(Quad(tuple((float(x), float(y)) for x, y in q)) for q in scene.lines)  # type: ignore[arg-type]
    texts = tuple(normalise_text(t) if digits_ascii else t for t in scene.line_texts)
    n = len(boxes)
    return lambda frame: OcrResult(boxes, texts, (conf,) * n, tuple(scene.line_scripts))
