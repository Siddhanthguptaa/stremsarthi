"""Live audio settings fed from ``Topic.CONFIG`` (``ConfigChange.diff``, dotted keys).

Handled keys (everything else is ignored):

* ``categories.spoken_phone|spoken_upi|spoken_address|spoken_other_pii.enabled`` (bool)
* ``categories.<those>.sensitivity`` (number in [0, 1]; lower means only the more certain rules mute)
* ``audio.languages`` (list of ``hi en kn ta te bn``; an empty, unknown or malformed list keeps the last good one)
* ``audio.mute_mode`` (``silence`` | ``bleep``)

Fail closed: the default is every spoken category ON; an unknown key, a wrong type or an out-of-range value is
ignored and never turns a category off. Applying a diff twice changes nothing (the API republishes the whole
config once at start). Fail-safe mutes of unverified voiced speech are not a category and are never filtered.
Nothing here logs a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contracts.enums import Category

SPOKEN_CATEGORIES = (
    Category.SPOKEN_PHONE,
    Category.SPOKEN_UPI,
    Category.SPOKEN_ADDRESS,
    Category.SPOKEN_OTHER_PII,
)
_BY_NAME = {c.value: c for c in SPOKEN_CATEGORIES}
LANGUAGES = ("hi", "en", "kn", "ta", "te", "bn")
MUTE_MODES = ("silence", "bleep")
DEFAULT_SENSITIVITY = 0.5
MAX_MIN_CONFIDENCE = 0.8  # sensitivity 0: only rules with at least this confidence mute


def min_confidence(sensitivity: float) -> float:
    """Sensitivity 0.5 (the default) accepts every rule hit (min 0.4); 1.0 accepts all; 0.0 needs 0.8."""
    return round((1.0 - sensitivity) * MAX_MIN_CONFIDENCE, 4)


class LiveAudioSettings:
    def __init__(
        self, languages: list[str] | tuple[str, ...] = LANGUAGES, mute_mode: str = "silence"
    ) -> None:
        self.enabled: dict[Category, bool] = dict.fromkeys(SPOKEN_CATEGORIES, True)
        self.sensitivity: dict[Category, float] = dict.fromkeys(SPOKEN_CATEGORIES, DEFAULT_SENSITIVITY)
        self.languages: tuple[str, ...] = tuple(languages) or LANGUAGES
        self.mute_mode: str = mute_mode if mute_mode in MUTE_MODES else "silence"
        self.version = 0  # bumps on every real change

    # ------------------------------------------------------------------ queries
    @property
    def lang_hint(self) -> str | None:
        """STT language hint: a single configured language, else ``None`` (the recogniser decides)."""
        return self.languages[0] if len(self.languages) == 1 else None

    def allows(self, category: Category, confidence: float) -> bool:
        """``False`` only for a spoken category that is OFF or a hit below that category's sensitivity floor.
        Any other category (``SPOKEN_UNVERIFIED``, a fail-safe) is always allowed."""
        if category not in self.enabled:
            return True
        if not self.enabled[category]:
            return False
        return confidence + 1e-9 >= min_confidence(self.sensitivity[category])

    # ------------------------------------------------------------------ updates
    def load_categories(self, categories: Any) -> None:
        """Initial values from ``Config.categories`` (a mapping ``Category -> CategoryConfig``)."""
        try:
            for cat in SPOKEN_CATEGORIES:
                cc = categories.get(cat)
                if cc is not None:
                    self.apply({f"categories.{cat.value}.enabled": cc.enabled})
                    self.apply({f"categories.{cat.value}.sensitivity": cc.sensitivity})
        except Exception:  # a malformed initial config leaves the safe defaults (everything on)
            return

    def fail_closed(self) -> bool:
        """Config feed lost an event or died: every spoken category back ON and no less sensitive than the default
        (more muting, never less). Languages and mute mode are kept (neither can leak). Returns whether it changed."""
        changed = False
        for cat in SPOKEN_CATEGORIES:
            if not self.enabled[cat]:
                self.enabled[cat] = True
                changed = True
            if self.sensitivity[cat] < DEFAULT_SENSITIVITY:
                self.sensitivity[cat] = DEFAULT_SENSITIVITY
                changed = True
        if changed:
            self.version += 1
        return changed

    def apply(self, diff: Mapping[str, Any]) -> bool:
        """Apply the keys we understand; returns whether anything changed."""
        changed = False
        try:
            items = list(dict(diff).items())
        except Exception:
            return False
        for key, val in items:
            try:
                changed |= self._apply_one(str(key), val)
            except Exception:  # one bad entry must not stop the others, nor switch anything off
                continue
        if changed:
            self.version += 1
        return changed

    def _apply_one(self, key: str, val: Any) -> bool:
        if key == "audio.languages":
            return self._set_languages(val)
        if key == "audio.mute_mode":
            if isinstance(val, str) and val in MUTE_MODES and val != self.mute_mode:
                self.mute_mode = val
                return True
            return False
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != "categories" or parts[1] not in _BY_NAME:
            return False
        cat = _BY_NAME[parts[1]]
        if parts[2] == "enabled":
            if isinstance(val, bool) and self.enabled[cat] != val:
                self.enabled[cat] = val
                return True
        elif parts[2] == "sensitivity":
            if isinstance(val, (int, float)) and not isinstance(val, bool) and 0.0 <= float(val) <= 1.0:
                if self.sensitivity[cat] != float(val):
                    self.sensitivity[cat] = float(val)
                    return True
        return False

    def _set_languages(self, val: Any) -> bool:
        if not isinstance(val, (list, tuple)) or not val:
            return False
        codes: list[str] = []
        for v in val:
            if not isinstance(v, str):
                return False
            code = v.strip().lower()
            if code not in LANGUAGES:
                return False  # one unknown entry invalidates the list: keep the last good one
            if code not in codes:
                codes.append(code)
        if tuple(codes) == self.languages:
            return False
        self.languages = tuple(codes)
        return True
