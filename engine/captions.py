"""Caption burn-in: ``CaptionEvent`` text drawn onto the output frame after every mask (T5.4).

New code (no upstream). Pillow with libraqm shapes Devanagari conjuncts and the Tamil, Telugu,
Kannada and Bengali clusters (``ImageFont.Layout.RAQM``); without raqm it falls back to the basic
layout and says so once (language codes only). Fonts are the Noto Sans files in ``engine/assets``
(SIL OFL-1.1, see ``engine/assets/NOTICE.md``) plus Atkinson Hyperlegible for Latin text.

Rules (docs/specs/engine.md 5.8):
- A caption is shown while ``pts_start <= frame pts <= pts_end`` (the delayed timeline: both are
  source PTS values, so the caption lines up with its audio however long the delay is).
- Text is drawn exactly as given. Redaction of PII tokens is the audio module's job.
- Placement is the bottom third inside a safe area (5 % side margins, 8 % bottom), at most two
  lines of ``captions.max_chars_per_line`` characters, a rounded semi-opaque box plus a dark
  outline on the text for legibility. A longer text is cut with "...".
- The rendered bitmap is cached per event: shaping happens once, in ``add`` (the bus thread) or on
  the first frame, never again. A frame costs one ROI multiply-add.

Privacy rules (the hard ones):
- Captions are the LAST compositing step. ``render`` only ever blends a constant colour over the
  pixels it is given (``out = in * (1 - alpha) + colour * alpha``, alpha at most 0.9), so it cannot
  expose anything a mask has hidden: it never reads the unmasked frame and never changes a pixel
  outside the caption box. The caller must pass the already painted frame.
- The caption text is never logged, published or put in an exception message. Logs carry counts and
  language codes only.
"""

from __future__ import annotations

import logging
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

from contracts import CaptionEvent, Config

log = logging.getLogger("streamsarthi.engine.captions")

ASSETS = Path(__file__).with_name("assets")
FONT_FILES: dict[str, Path] = {
    "latin": ASSETS / "AtkinsonHyperlegibleNext-wght.ttf",
    "deva": ASSETS / "NotoSansDevanagari-VF.ttf",
    "knda": ASSETS / "NotoSansKannada-VF.ttf",
    "taml": ASSETS / "NotoSansTamil-VF.ttf",
    "telu": ASSETS / "NotoSansTelugu-VF.ttf",
    "beng": ASSETS / "NotoSansBengali-VF.ttf",
}
_SCRIPT_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x0900, 0x097F, "deva"),
    (0xA8E0, 0xA8FF, "deva"),
    (0x0980, 0x09FF, "beng"),
    (0x0B80, 0x0BFF, "taml"),
    (0x0C00, 0x0C7F, "telu"),
    (0x0C80, 0x0CFF, "knda"),
)
# BCP-47 tag per script (raqm picks language-specific shaping features from it)
_SCRIPT_LANG = {"latin": "en", "deva": "hi", "beng": "bn", "taml": "ta", "telu": "te", "knda": "kn"}
# Event languages that share a script with a different default tag (Marathi, Nepali, Assamese)
_LANG_SCRIPT = {"hi": "deva", "mr": "deva", "ne": "deva", "bn": "beng", "as": "beng",
                "ta": "taml", "te": "telu", "kn": "knda"}  # fmt: skip
_WEIGHT = {"latin": 600.0, "deva": 500.0, "beng": 500.0, "taml": 500.0, "telu": 500.0, "knda": 500.0}
_VIRAMAS = frozenset("्্்్್")
_ZERO_WIDTH = frozenset("‌‍")

MAX_LINES = 2
MAX_PENDING = 128  # events kept (text lives in RAM only, until its window has passed)
MAX_PREPARED = 24  # cached bitmaps
LATE_PRUNE_S = 1.0  # an event this far behind the newest rendered pts is dropped

FILL_BGR = (255, 255, 255)
OUTLINE_BGR = (0, 0, 0)
BOX_RGB = (18, 22, 28)
BOX_ALPHA = 0.72  # semi-opaque box: legible on any video, never fully hides it
ELLIPSIS = "..."

# Layout fractions of the canvas
SIDE_MARGIN = 0.05
BOTTOM_MARGIN = 0.08
FONT_FRACTION = 0.05  # font size / canvas height: 36 px at 720p
MIN_FONT_FRACTION = 0.034


def raqm_available() -> bool:
    return bool(features.check("raqm"))


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, script in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return script
    return None


def segment(word: str) -> list[tuple[str, str]]:
    """Split a word into (script, text) runs. ASCII, digits and punctuation use the Latin font (the Noto
    Indic fonts do not all carry them); combining marks and joiners stay with the character before."""
    runs: list[tuple[str, list[str]]] = []
    for ch in word:
        script = script_of(ch)
        if script is None:
            if runs and (unicodedata.category(ch).startswith("M") or ch in _ZERO_WIDTH):
                script = runs[-1][0]
            else:
                script = "latin"
        if runs and runs[-1][0] == script:
            runs[-1][1].append(ch)
        else:
            runs.append((script, [ch]))
    return [(s, "".join(chars)) for s, chars in runs]


def clusters(word: str) -> list[str]:
    """Split a word into user-perceived characters (a base plus its marks, a virama joining the next)."""
    out: list[str] = []
    for ch in word:
        joins = (
            out
            and (
                unicodedata.category(ch).startswith("M")
                or ch in _ZERO_WIDTH
                or out[-1][-1] in _VIRAMAS
                or (len(out[-1]) > 1 and out[-1][-1] in _ZERO_WIDTH and out[-1][-2] in _VIRAMAS)
            )
        )  # fmt: skip
        if joins:
            out[-1] += ch
        else:
            out.append(ch)
    return out


def char_count(text: str) -> int:
    """Length in user-perceived characters (marks and joiners do not count)."""
    return sum(1 for ch in text if not unicodedata.category(ch).startswith("M") and ch not in _ZERO_WIDTH)


def _load_font(script: str, size: int, engine: Any) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_FILES[script]), size, layout_engine=engine)
    try:
        values = []
        for axis in font.get_variation_axes():
            name = axis["name"].decode() if isinstance(axis["name"], bytes) else str(axis["name"])
            wanted = {"Weight": _WEIGHT[script], "Width": 100.0}.get(name, axis["default"])
            values.append(min(max(wanted, axis["minimum"]), axis["maximum"]))
        font.set_variation_by_axes(values)
    except (OSError, AttributeError, KeyError):  # static font or FreeType without variation support
        pass
    return font


@dataclass(slots=True)
class Prepared:
    """One caption, shaped and rasterised once. ``premul`` and ``inv`` are the box-sized blend operands."""

    x: int
    y: int
    premul: np.ndarray  # H x W x 3 uint8, colour * alpha (BGR)
    inv: np.ndarray  # H x W x 3 uint8, 255 - alpha
    lines: int
    truncated: bool

    @property
    def rect(self) -> tuple[int, int, int, int]:
        h, w = self.premul.shape[:2]
        return self.x, self.y, w, h


@dataclass(slots=True)
class _Entry:
    event: CaptionEvent
    serial: int
    prepared: Prepared | None = None


class CaptionRenderer:
    """Holds pending ``CaptionEvent``s and burns the one active at a frame's PTS onto the frame."""

    def __init__(
        self,
        config: Config | None = None,
        width: int | None = None,
        height: int | None = None,
        *,
        use_raqm: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        canvas = config.canvas if config else (1280, 720)
        self.width, self.height = int(width or canvas[0]), int(height or canvas[1])
        if self.width < 160 or self.height < 90:
            raise ValueError("caption canvas too small")
        self.raqm = raqm_available() if use_raqm is None else bool(use_raqm) and raqm_available()
        if not self.raqm and use_raqm is not False:
            log.warning("libraqm unavailable: Indic captions fall back to basic layout (no conjuncts)")
        self._engine = ImageFont.Layout.RAQM if self.raqm else ImageFont.Layout.BASIC
        self._enabled = bool(config.captions.enabled) if enabled is None and config else bool(enabled)
        self.max_chars = int(config.captions.max_chars_per_line) if config else 42
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._entries: list[_Entry] = []
        self._serial = 0
        self._newest_pts = float("-inf")
        self._lock = threading.Lock()
        self.stats = {"added": 0, "late": 0, "prepared": 0, "drawn": 0, "truncated": 0, "errors": 0}

    # ------------------------------------------------------------------ control

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = bool(value)

    def apply_config_key(self, key: str, value: Any) -> bool:
        """Runtime toggles from ``ConfigChange.diff``. True if the key was one of ours; bad values raise
        ``ValueError`` (the session counts them as ignored)."""
        if key == "captions.enabled":
            if not isinstance(value, bool):
                raise ValueError("captions.enabled must be a boolean")
            self.set_enabled(value)
            return True
        if key == "captions.max_chars_per_line":
            if isinstance(value, bool) or not isinstance(value, int) or not 10 <= value <= 80:
                raise ValueError("captions.max_chars_per_line must be an integer from 10 to 80")
            with self._lock:
                self.max_chars = value
                for entry in self._entries:
                    entry.prepared = None  # relayout on next use
            return True
        return False

    def clear(self) -> None:
        """Forget every pending caption (the PTS clock restarted: their times are void)."""
        with self._lock:
            self._entries.clear()
            self._newest_pts = float("-inf")

    # ------------------------------------------------------------------ events

    def add(self, event: CaptionEvent) -> None:
        """Queue a caption. Shaping happens here when captions are on, so the frame path never shapes."""
        with self._lock:
            replaces = any(abs(e.event.pts_start - event.pts_start) < 1e-6 for e in self._entries)
            if event.pts_end < self._newest_pts - LATE_PRUNE_S and not replaces:
                # a stale event is dropped, but NEVER a replacement: a redaction (often a zero-length event)
                # for a caption that started long ago and is still on screen must take effect at once
                self.stats["late"] += 1
                return
            self._serial += 1
            entry = _Entry(event, self._serial)
            # A later event for the same utterance (same pts_start) REPLACES the earlier one at once, final or
            # not: the caption service republishes a redacted text when a mute span arrives late, and the
            # old text must not be drawn on the next frame, nor reappear after the new one ends.
            self._entries = [e for e in self._entries if abs(e.event.pts_start - event.pts_start) >= 1e-6]
            self._entries.append(entry)
            if len(self._entries) > MAX_PENDING:
                self._entries.sort(key=lambda e: (e.event.pts_end, e.serial))
                del self._entries[: len(self._entries) - MAX_PENDING]
            self.stats["added"] += 1
        if self._enabled:
            self._prepare_entry(entry)

    def _prepare_entry(self, entry: _Entry) -> Prepared | None:
        try:
            prepared = self.prepare(entry.event)
        except Exception as exc:  # a bad font or text must not reach the frame path
            self.stats["errors"] += 1
            log.warning("caption layout failed: %s (lang=%s)", type(exc).__name__, entry.event.lang)
            return None
        with self._lock:
            entry.prepared = prepared
            self.stats["prepared"] += 1
            self.stats["truncated"] += int(prepared.truncated)
            cached = [e for e in self._entries if e.prepared is not None]
            if len(cached) > MAX_PREPARED:
                for old in sorted(cached, key=lambda e: e.serial)[: len(cached) - MAX_PREPARED]:
                    old.prepared = None
        return prepared

    def active(self, pts: float) -> CaptionEvent | None:
        """The caption to show at ``pts``: the covering event that started last (the newest revision wins)."""
        with self._lock:
            entry = self._pick(pts)
            return entry.event if entry else None

    def _pick(self, pts: float) -> _Entry | None:
        best: _Entry | None = None
        for e in self._entries:
            if e.event.pts_start <= pts <= e.event.pts_end and (
                best is None or (e.event.pts_start, e.serial) > (best.event.pts_start, best.serial)
            ):
                best = e
        return best

    # ------------------------------------------------------------------ render (frame path)

    def render(self, image: np.ndarray, pts: float) -> bool:
        """Burn the caption active at ``pts`` into ``image`` in place; True if one was drawn.

        ``image`` must be the finished, masked frame (BGR uint8, canvas sized). Nothing outside the
        caption box changes, and inside it every pixel only moves toward a constant colour."""
        if not self._enabled:
            return False
        if image.shape != (self.height, self.width, 3) or image.dtype != np.uint8:
            raise ValueError("caption target must be a canvas-sized BGR uint8 frame")
        if not image.flags.writeable:
            raise ValueError("caption target is read-only")
        with self._lock:
            self._newest_pts = max(self._newest_pts, pts)
            horizon = self._newest_pts - LATE_PRUNE_S
            if self._entries and any(e.event.pts_end < horizon for e in self._entries):
                self._entries = [e for e in self._entries if e.event.pts_end >= horizon]
            entry = self._pick(pts)
            prepared = entry.prepared if entry else None
        if entry is None:
            return False
        if prepared is None:  # captions were switched on after the event arrived
            prepared = self._prepare_entry(entry)
            if prepared is None:
                return False
        x, y, w, h = prepared.rect
        roi = image[y : y + h, x : x + w]
        cv2.multiply(roi, prepared.inv, dst=roi, scale=1.0 / 255.0)
        cv2.add(roi, prepared.premul, dst=roi)
        self.stats["drawn"] += 1
        return True

    # ------------------------------------------------------------------ layout and raster

    def _font(self, script: str, size: int) -> ImageFont.FreeTypeFont:
        key = (script, size)
        font = self._fonts.get(key)
        if font is None:
            font = self._fonts[key] = _load_font(script, size, self._engine)
        return font

    def _tag(self, script: str, lang: str) -> dict[str, str]:
        if not self.raqm:
            return {}
        return {"language": lang if _LANG_SCRIPT.get(lang) == script else _SCRIPT_LANG[script]}

    def _width(self, runs: list[tuple[str, str]], size: int, lang: str) -> float:
        return sum(self._font(s, size).getlength(t, **self._tag(s, lang)) for s, t in runs)

    def layout(self, text: str, lang: str = "hi") -> tuple[int, list[str], bool]:
        """(font size, lines, truncated) for ``text``: word wrap by characters and pixels, shrinking the
        font a little before giving up, then cutting with an ellipsis. Pure function of text and canvas."""
        # Lines are drawn as given: each newline-separated line is wrapped on its own (it stays one line when it
        # fits), so a caption of two 42-character lines is never reflowed.
        paragraphs = [p.split() for p in text.split(chr(10))]
        paragraphs = [p for p in paragraphs if p]
        if not paragraphs:
            return self._base_size(), [], False
        base = self._base_size()
        floor = max(8, round(self.height * MIN_FONT_FRACTION))
        pad_x = self._pad(base)[0]
        max_px = self.width * (1.0 - 2 * SIDE_MARGIN) - 2 * pad_x

        chars_only = self._wrap_all(paragraphs, base, lang, self.max_chars, float("inf"))
        sizes = (
            [base]
            if len(chars_only) > MAX_LINES
            else sorted({base, round(base * 0.9), round(base * 0.8), floor}, reverse=True)
        )
        lines: list[str] = []
        size = base
        for size in (s for s in sizes if s >= floor):
            lines = self._wrap_all(paragraphs, size, lang, self.max_chars, max_px)
            if len(lines) <= MAX_LINES:
                return size, lines, False
        return size, self._truncate(lines, size, lang, max_px), True

    def _base_size(self) -> int:
        return max(10, round(self.height * FONT_FRACTION))

    @staticmethod
    def _pad(size: int) -> tuple[int, int]:
        return round(0.5 * size), round(0.3 * size)

    def _wrap_all(
        self, paragraphs: list[list[str]], size: int, lang: str, max_chars: int, max_px: float
    ) -> list[str]:
        out: list[str] = []
        for words in paragraphs:
            out.extend(self._wrap(words, size, lang, max_chars, max_px))
        return out

    def _wrap(self, words: list[str], size: int, lang: str, max_chars: int, max_px: float) -> list[str]:
        space = self._font("latin", size).getlength(" ")
        lines: list[str] = []
        cur: list[str] = []
        cur_chars, cur_px = 0, 0.0

        def flush() -> None:
            nonlocal cur, cur_chars, cur_px
            if cur:
                lines.append(" ".join(cur))
            cur, cur_chars, cur_px = [], 0, 0.0

        for word in words:
            chunks = self._chunks(word, size, lang, max_chars, max_px)
            for i, chunk in enumerate(chunks):
                cc, px = char_count(chunk), self._width(segment(chunk), size, lang)
                if cur and (cur_chars + 1 + cc > max_chars or cur_px + space + px > max_px):
                    flush()
                cur_px += px + (space if cur else 0.0)
                cur_chars += cc + (1 if cur else 0)
                cur.append(chunk)
                if i < len(chunks) - 1:
                    flush()  # a word split over lines never shares a line with its own next piece
        flush()
        return lines

    def _chunks(self, word: str, size: int, lang: str, max_chars: int, max_px: float) -> list[str]:
        if char_count(word) <= max_chars and self._width(segment(word), size, lang) <= max_px:
            return [word]
        out: list[str] = []
        cur = ""
        for cluster in clusters(word):
            trial = cur + cluster
            if cur and (char_count(trial) > max_chars or self._width(segment(trial), size, lang) > max_px):
                out.append(cur)
                cur = cluster
            else:
                cur = trial
        if cur:
            out.append(cur)
        return out

    def _truncate(self, lines: list[str], size: int, lang: str, max_px: float) -> list[str]:
        kept = lines[:MAX_LINES]
        last = kept[-1]
        dots = self._width([("latin", ELLIPSIS)], size, lang)
        parts = clusters(last)
        while parts and (
            char_count("".join(parts)) + len(ELLIPSIS) > self.max_chars
            or self._width(segment("".join(parts)), size, lang) + dots > max_px
        ):
            parts.pop()
        kept[-1] = "".join(parts).rstrip() + ELLIPSIS
        return kept

    def prepare(self, event: CaptionEvent) -> Prepared:
        """Shape and rasterise one event into its cached blend bitmap (this is the only slow step)."""
        size, lines, truncated = self.layout(event.text, event.lang)
        if not lines:
            return Prepared(0, 0, np.zeros((0, 0, 3), np.uint8), np.zeros((0, 0, 3), np.uint8), 0, False)
        line_runs = [
            [run for word in line.split(" ") for run in segment(word) + [("latin", " ")]][:-1]
            for line in lines
        ]
        used = {s for runs in line_runs for s, _ in runs} | {"latin"}
        ascent = max(self._font(s, size).getmetrics()[0] for s in used)
        descent = max(self._font(s, size).getmetrics()[1] for s in used)
        line_h = ascent + descent + round(0.12 * size)
        stroke = max(2, round(size / 16))
        pad_x, pad_y = self._pad(size)
        text_w = max(self._width(runs, size, event.lang) for runs in line_runs)
        box_w = min(self.width, int(np.ceil(text_w)) + 2 * (pad_x + stroke))
        box_h = line_h * len(lines) - round(0.12 * size) + 2 * (pad_y + stroke)
        box_h = min(self.height, box_h)

        fill_mask = Image.new("L", (box_w, box_h), 0)
        outline_mask = Image.new("L", (box_w, box_h), 0)
        fill_draw, outline_draw = ImageDraw.Draw(fill_mask), ImageDraw.Draw(outline_mask)
        for i, runs in enumerate(line_runs):
            line_w = self._width(runs, size, event.lang)
            cursor = (box_w - line_w) / 2.0
            baseline = pad_y + stroke + ascent + i * line_h
            for script, text in runs:
                font = self._font(script, size)
                kw = self._tag(script, event.lang)
                outline_draw.text(
                    (cursor, baseline),
                    text,
                    font=font,
                    fill=255,
                    anchor="ls",
                    stroke_width=stroke,
                    stroke_fill=255,
                    **kw,
                )
                fill_draw.text((cursor, baseline), text, font=font, fill=255, anchor="ls", **kw)
                cursor += font.getlength(text, **kw)

        box_mask = Image.new("L", (box_w, box_h), 0)
        ImageDraw.Draw(box_mask).rounded_rectangle(
            (0, 0, box_w - 1, box_h - 1), radius=round(0.35 * size), fill=round(BOX_ALPHA * 255)
        )
        layer = Image.new("RGBA", (box_w, box_h), (*BOX_RGB, 0))
        layer.putalpha(box_mask)
        for colour, mask in (((0, 0, 0), outline_mask), ((255, 255, 255), fill_mask)):
            top = Image.new("RGBA", (box_w, box_h), (*colour, 0))
            top.putalpha(mask)
            layer = Image.alpha_composite(layer, top)

        rgba = np.asarray(layer, np.uint16)
        alpha = rgba[:, :, 3:4]
        premul = ((rgba[:, :, :3] * alpha + 127) // 255).astype(np.uint8)[:, :, ::-1]  # RGB -> BGR
        inv = np.repeat((255 - alpha).astype(np.uint8), 3, axis=2)
        x = (self.width - box_w) // 2
        bottom = int(round(self.height * (1.0 - BOTTOM_MARGIN)))
        y = max(0, bottom - box_h)  # bottom edge on the safe line, inside the bottom third
        return Prepared(x, y, np.ascontiguousarray(premul), np.ascontiguousarray(inv), len(lines), truncated)

    def text_mask(self, text: str, lang: str, size: int | None = None) -> np.ndarray:
        """Single-line coverage mask (uint8, 0 to 255) of ``text`` with no box and no outline. Used by the
        shaping tests to compare against reference renders; also handy for tools."""
        size = size or self._base_size()
        runs = [run for word in text.split(" ") for run in segment(word) + [("latin", " ")]][:-1]
        used = {s for s, _ in runs} or {"latin"}
        ascent = max(self._font(s, size).getmetrics()[0] for s in used)
        descent = max(self._font(s, size).getmetrics()[1] for s in used)
        width = int(np.ceil(self._width(runs, size, lang))) + 8
        mask = Image.new("L", (width, ascent + descent + 8), 0)
        draw = ImageDraw.Draw(mask)
        cursor = 4.0
        for script, chunk in runs:
            font = self._font(script, size)
            kw = self._tag(script, lang)
            draw.text((cursor, 4 + ascent), chunk, font=font, fill=255, anchor="ls", **kw)
            cursor += font.getlength(chunk, **kw)
        return np.asarray(mask, np.uint8).copy()
