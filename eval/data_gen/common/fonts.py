"""Noto Sans fonts for the SPECIMEN renderers (OFL-1.1, SHA-256 pinned, see ``eval/fonts/NOTICE.md``).

Indic text must be shaped (conjuncts, reordering), so every Indic render uses Pillow's raqm layout engine
(``PIL.features.check("raqm")``). Without raqm, ``load_font`` raises ``RaqmMissing`` for Indic scripts
and generators skip those buckets with a clear error instead of drawing wrong glyphs.

The fonts are the Google Fonts variable builds (weight and width axes); ``load_font`` picks the weight.
"""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont, features

FONT_DIR = Path(__file__).resolve().parents[2] / "fonts"
GOOGLE_FONTS_COMMIT = "869789e84da92782c144d83d69b64cec54905b5f"
INDIC_SCRIPTS = ("devanagari", "kannada", "tamil", "telugu", "bengali")
SCRIPTS = ("latin", *INDIC_SCRIPTS)


@dataclass(frozen=True)
class FontPin:
    script: str
    family_dir: str  # directory under google/fonts/ofl/
    upstream_name: str  # file name in that directory
    local_name: str
    sha256: str

    @property
    def url(self) -> str:
        return (
            f"https://raw.githubusercontent.com/google/fonts/{GOOGLE_FONTS_COMMIT}/ofl/{self.family_dir}/"
            + urllib.parse.quote(self.upstream_name)
        )

    @property
    def path(self) -> Path:
        return FONT_DIR / self.local_name


FONT_PINS: dict[str, FontPin] = {
    p.script: p
    for p in (
        FontPin(
            "latin",
            "notosans",
            "NotoSans[wdth,wght].ttf",
            "NotoSans-VF.ttf",
            "bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d",
        ),
        FontPin(
            "devanagari",
            "notosansdevanagari",
            "NotoSansDevanagari[wdth,wght].ttf",
            "NotoSansDevanagari-VF.ttf",
            "14ec4af41f27482216d1c2229f417ff9b1425e1babb014e57d1d40d03229853e",
        ),
        FontPin(
            "kannada",
            "notosanskannada",
            "NotoSansKannada[wdth,wght].ttf",
            "NotoSansKannada-VF.ttf",
            "cca4f3b3a8cb12fb261f1b43baf5d2f7f59d90fe123d41f0065ed3a183997ec9",
        ),
        FontPin(
            "tamil",
            "notosanstamil",
            "NotoSansTamil[wdth,wght].ttf",
            "NotoSansTamil-VF.ttf",
            "aa3a9b321f4b0bb2c40203ffbde9af89713227866e0e13f76e5b9eeea727cf88",
        ),
        FontPin(
            "telugu",
            "notosanstelugu",
            "NotoSansTelugu[wdth,wght].ttf",
            "NotoSansTelugu-VF.ttf",
            "e618af7bf999df192ed4f388eba2e563f2b5015034e9cbb317b5bd793bd7334d",
        ),
        FontPin(
            "bengali",
            "notosansbengali",
            "NotoSansBengali[wdth,wght].ttf",
            "NotoSansBengali-VF.ttf",
            "dcd42978094e584a849c84a51450eeac40c8826057d566ea6d4b9627a403a05a",
        ),
    )
}


class RaqmMissing(RuntimeError):
    """Pillow was built without libraqm, so Indic scripts cannot be shaped correctly."""


class FontError(RuntimeError):
    """A pinned font is missing or does not match its SHA-256."""


def raqm_available() -> bool:
    return bool(features.check("raqm"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_fonts(scripts: tuple[str, ...] = SCRIPTS) -> list[str]:
    """Problems with the pinned fonts (empty list = all present and hash-correct)."""
    problems = []
    for s in scripts:
        pin = FONT_PINS[s]
        if not pin.path.exists():
            problems.append(f"{pin.local_name}: missing")
        elif sha256_file(pin.path) != pin.sha256:
            problems.append(f"{pin.local_name}: sha256 mismatch")
    return problems


def fetch_fonts(scripts: tuple[str, ...] = SCRIPTS) -> list[str]:
    """Download missing fonts from the pinned google/fonts commit and verify the hash. Returns problems."""
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    problems = []
    for s in scripts:
        pin = FONT_PINS[s]
        if pin.path.exists() and sha256_file(pin.path) == pin.sha256:
            continue
        try:
            req = urllib.request.Request(pin.url, headers={"User-Agent": "font-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 - pinned https URL
                data = r.read()
        except OSError as exc:
            problems.append(f"{pin.local_name}: download failed ({exc})")
            continue
        if hashlib.sha256(data).hexdigest() != pin.sha256:
            problems.append(f"{pin.local_name}: downloaded file has the wrong sha256")
            continue
        pin.path.write_bytes(data)
    return problems


def ensure_fonts(scripts: tuple[str, ...] = SCRIPTS) -> None:
    problems = verify_fonts(scripts)
    if problems:
        problems = fetch_fonts(scripts)
    if problems:
        raise FontError("; ".join(problems))


@lru_cache(maxsize=256)
def load_font(script: str, size: int, weight: int = 400, width: int = 100) -> ImageFont.FreeTypeFont:
    """Noto Sans for `script` at `size` px, `weight` (100-900) and `width` (62.5-100 where the axis exists)."""
    if script not in FONT_PINS:
        raise ValueError(f"unknown script {script!r}")
    if script in INDIC_SCRIPTS and not raqm_available():
        raise RaqmMissing("Pillow has no libraqm: cannot shape " + script)
    pin = FONT_PINS[script]
    if not pin.path.exists():
        raise FontError(f"{pin.local_name} missing; run ensure_fonts()")
    engine = ImageFont.Layout.RAQM if raqm_available() else ImageFont.Layout.BASIC
    f = ImageFont.truetype(str(pin.path), size, layout_engine=engine)
    try:
        axes = f.get_variation_axes()
        values = []
        for a in axes:
            name = a["name"].decode() if isinstance(a["name"], bytes) else str(a["name"])
            if "weight" in name.lower():
                values.append(max(a["minimum"], min(a["maximum"], weight)))
            elif "width" in name.lower():
                values.append(max(a["minimum"], min(a["maximum"], width)))
            else:
                values.append(a["default"])
        f.set_variation_by_axes(values)
    except (OSError, AttributeError, KeyError):  # no variation support: default (regular) weight
        pass
    return f
