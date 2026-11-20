"""Pre-rendered privacy slates (English and Hindi) and the capture high-water-mark fence.

Adapted from IRL Shield ``shield/presentation/renderer.py`` (MIT, Copyright (c) 2026 Elan Nash;
``engine/THIRD_PARTY_LICENSES/irl-shield/LICENSE``). Kept: slates are rasterised once at start
and served from a cache; the opaque colour tokens; the original geometric shield mark; "after a
hold a release needs a frame newer than the capture high-water mark". Changed: four slate kinds
(HOLD, PROTECT, TRANSPORT, FAILURE) with the copy taken from ``Config.hold`` for HOLD; every slate
carries an English and a Hindi line (Pillow with libraqm so Devanagari conjuncts shape correctly,
basic layout and a one-time warning when raqm is missing); no badge, skins or tally.

Fonts (SIL OFL 1.1, licence texts next to them in ``engine/assets/``): Atkinson Hyperlegible Next
for Latin, Noto Sans Devanagari for Hindi. Pillow rasterises glyphs only at construction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

from contracts import Config

from .control import HOLD, PROTECT

log = logging.getLogger("streamsarthi.engine.slate")

ASSETS = Path(__file__).with_name("assets")
LATIN_FONT = ASSETS / "AtkinsonHyperlegibleNext-wght.ttf"
DEVANAGARI_FONT = ASSETS / "NotoSansDevanagari-VF.ttf"

SlateKind = Literal["HOLD", "PROTECT", "TRANSPORT", "FAILURE"]
SLATE_KINDS: tuple[SlateKind, ...] = ("HOLD", "PROTECT", "TRANSPORT", "FAILURE")

# Hold sources that mean "the stream's own plumbing is down" rather than "the streamer paused".
TRANSPORT_REASONS = frozenset({"auto_transport", "auto_egress"})
# HOLD reasons the deadline engine derives (not an active hold source): retro window and fence.
DERIVED_HOLD_REASONS = frozenset({"hold-fence", "retro-hold"})

# RGB tokens: fixed, opaque colours over arbitrary video.
FIELD = (18, 22, 28)
INK = (243, 246, 249)
MUTED = (205, 214, 224)
GREEN = (126, 225, 166)
AMBER = (244, 199, 123)

_PROTECT_HI = "गोपनीयता की जाँच हो रही है"


@dataclass(frozen=True, slots=True)
class SlateText:
    headline_en: str
    headline_hi: str
    sub_en: str
    sub_hi: str


def slate_texts(config: Config | None = None) -> dict[SlateKind, SlateText]:
    """Slate copy. HOLD uses ``Config.hold.slate_text_en/hi``; the rest is engine-defined."""
    hold_en = config.hold.slate_text_en if config else "Privacy hold"
    hold_hi = config.hold.slate_text_hi if config else "निजता सुरक्षा चालू"
    protect = SlateText(
        "Checking privacy coverage",
        _PROTECT_HI,
        "Video resumes automatically.",
        "वीडियो अपने आप फिर शुरू होगा।",
    )
    return {
        "HOLD": SlateText(hold_en, hold_hi, "Back when the streamer resumes", "स्ट्रीमर के लौटने पर वापस आएगा"),
        "PROTECT": protect,
        "TRANSPORT": SlateText(
            "Reconnecting video",
            "वीडियो दोबारा जुड़ रहा है",
            "Paused for privacy. Resumes automatically.",
            "निजता के लिए रुका है। अपने आप फिर शुरू होगा।",
        ),
        "FAILURE": SlateText(
            "Technical problem",
            "तकनीकी समस्या",
            "Please stand by. Video resumes automatically.",
            "कृपया प्रतीक्षा करें। वीडियो अपने आप फिर शुरू होगा।",
        ),
    }


def raqm_available() -> bool:
    return bool(features.check("raqm"))


def _load_font(path: Path, size: int, axes: dict[str, float], engine: Any) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), size, layout_engine=engine)
    try:
        values = []
        for axis in font.get_variation_axes():
            name = axis["name"].decode() if isinstance(axis["name"], bytes) else str(axis["name"])
            wanted = axes.get(name, axis["default"])
            values.append(min(max(wanted, axis["minimum"]), axis["maximum"]))
        font.set_variation_by_axes(values)
    except (OSError, AttributeError, KeyError):  # static font or FreeType without variation support
        pass
    return font


class SlateStore:
    """The four slates as read-only BGR arrays, rendered once."""

    def __init__(
        self,
        config: Config | None = None,
        width: int = 1280,
        height: int = 720,
        *,
        use_raqm: bool | None = None,
    ) -> None:
        if width < 160 or height < 90:
            raise ValueError("slate canvas too small")
        self.width, self.height = width, height
        self._scale = height / 720
        self.raqm = raqm_available() if use_raqm is None else bool(use_raqm) and raqm_available()
        if not self.raqm:
            log.warning(
                "libraqm unavailable: Devanagari slate text falls back to basic layout (no conjuncts)"
            )
        self.texts = slate_texts(config)
        self._engine = ImageFont.Layout.RAQM if self.raqm else ImageFont.Layout.BASIC
        self._fonts: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}
        self._slates: dict[SlateKind, np.ndarray] = {kind: self._make(kind) for kind in SLATE_KINDS}
        for slate in self._slates.values():
            slate.setflags(write=False)

    def get(self, kind: SlateKind) -> np.ndarray:
        """The shared read-only slate. Copy before mutating."""
        return self._slates[kind]

    def _px(self, value: float) -> int:
        return max(1, round(value * self._scale))

    def _font(self, script: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
        key = (script, size, weight)
        if key not in self._fonts:
            if script == "hi":
                self._fonts[key] = _load_font(
                    DEVANAGARI_FONT, self._px(size), {"Weight": weight, "Width": 100}, self._engine
                )
            else:
                self._fonts[key] = _load_font(LATIN_FONT, self._px(size), {"Weight": weight}, self._engine)
        return self._fonts[key]

    def _make(self, kind: SlateKind) -> np.ndarray:
        text = self.texts[kind]
        canvas = Image.new("RGB", (self.width, self.height), FIELD)
        draw = ImageDraw.Draw(canvas)
        cx = self.width // 2
        accent = AMBER if kind in ("PROTECT", "TRANSPORT", "FAILURE") else GREEN
        # A small geometric shield: original project artwork (from IRL Shield, MIT).
        mark = [
            (cx + round(x * self._scale), self._px(y))
            for x, y in ((0, 96), (26, 106), (23, 135), (0, 153), (-23, 135), (-26, 106))
        ]
        draw.polygon(mark, fill=accent)
        kw: dict[str, Any] = {"anchor": "mt"}
        draw.text((cx, self._px(205)), text.headline_en, font=self._font("en", 60, 650), fill=INK, **kw)
        hi_kw = dict(kw, language="hi") if self.raqm else kw
        draw.text((cx, self._px(290)), text.headline_hi, font=self._font("hi", 56, 600), fill=INK, **hi_kw)
        draw.text((cx, self._px(430)), text.sub_en, font=self._font("en", 38, 450), fill=MUTED, **kw)
        draw.text((cx, self._px(490)), text.sub_hi, font=self._font("hi", 36, 400), fill=MUTED, **hi_kw)
        return np.asarray(canvas)[:, :, ::-1].copy()


def hold_slate_kind(reason: str, last: SlateKind = "HOLD") -> SlateKind:
    """Which slate a HOLD reason shows. Fence and retro reasons keep the slate already on screen."""
    if reason in TRANSPORT_REASONS:
        return "TRANSPORT"
    if reason in DERIVED_HOLD_REASONS:
        return last
    return "HOLD"


class SlatePresenter:
    """Chooses slate or pass-through per frame and enforces the capture high-water-mark fence.

    After a HOLD frame, every frame captured up to the high-water mark at that moment stays
    slated, so buffered pre-hold frames cannot leak when the hold is released (docs/specs/engine.md
    5.3). ``fence_on_protect`` extends the fence to PROTECT; the default is False because coverage
    is decided per frame (a one-frame blip must not slate four seconds of video). The deadline
    engine fences holds as well; this is the second layer at the output.
    """

    def __init__(self, store: SlateStore, *, fence_on_protect: bool = False) -> None:
        self.store = store
        self.fence_on_protect = fence_on_protect
        self.blocked_through = -1
        self.last_hold_kind: SlateKind = "HOLD"
        self.fenced_frames = 0

    def present(self, state: str, reason: str, seq: int, capture_highwater: int) -> SlateKind | None:
        """The slate to show for this frame, or None when the frame may pass (masks painted)."""
        if state == HOLD:
            if reason not in DERIVED_HOLD_REASONS:
                # an active hold: the mark follows the newest capture until the hold ends. Frames
                # slated only because of the fence or the retro window must not move it (a release
                # would otherwise be pushed out by the time those frames are emitted)
                self.blocked_through = max(self.blocked_through, capture_highwater)
            kind = hold_slate_kind(reason, self.last_hold_kind)
            self.last_hold_kind = kind
            return kind
        if state == PROTECT:
            if self.fence_on_protect:
                self.blocked_through = max(self.blocked_through, capture_highwater)
            return "PROTECT"
        if seq <= self.blocked_through:
            self.fenced_frames += 1
            return self.last_hold_kind
        return None

    def reset(self) -> None:
        """New session clock: sequence numbers restart, so the fence must too."""
        self.blocked_through = -1
