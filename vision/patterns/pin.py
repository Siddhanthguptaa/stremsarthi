"""Indian PIN codes in OCR text, with the cue words that make a bare 6-digit number a PIN.

A PIN is 6 digits, first digit 1-9 (spec), written ``560001`` or ``560 001``. A bare 6-digit number is
common (scores, order numbers, amounts), so the detector masks one only with a cue nearby (``PIN``,
``pincode``, an address word) or in a short box at sensitivity >= 0.5. As an extra false-positive filter the
first two digits must be a real postal circle (11-85 or 90-99, the Army Post Office range).

Pure text; the cue sets are lower-case and compared after ``casefold``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Not part of a longer number or an alphanumeric id: no digit, letter, "#", "." or "," glued before it, no
# digit group before or after it ("1800 123 456", "Order #AB123456", "12,99,999"). A hyphen before is fine
# ("Pincode -560001").
PIN_RE = re.compile(r"(?<![\d.,A-Za-z#/])(?<!\d\s)[1-9]\d{2}\s?\d{3}(?!\d|[.,]\d|\s\d|[A-Za-z/])")

# strong cues: the number is labelled as a PIN (Latin cues match whole words, Indic cues as substrings)
PIN_CUES: tuple[str, ...] = (
    "pin",
    "pincode",
    "postal",
    "zip",
    "पिन",
    "पिनकोड",
    "பின்",
    "పిన్",
    "ಪಿನ್",
    "পিন",
)
# address cues: the number sits in an address block
ADDRESS_CUES: tuple[str, ...] = (
    "road",
    "street",
    "nagar",
    "colony",
    "sector",
    "near",
    "dist",
    "district",
    "city",
    "village",
    "post office",
    "taluk",
    "tehsil",
    "layout",
    "plot",
    "flat",
    "ward",
    "bengaluru",
    "bangalore",
    "mumbai",
    "delhi",
    "chennai",
    "hyderabad",
    "kolkata",
    "pune",
    "karnataka",
    "maharashtra",
    "kerala",
    "gujarat",
    "rajasthan",
    "bihar",
    "marg",
    "मार्ग",
    "नगर",
    "रोड",
    "गली",
    "जिला",
    "गांव",
    "पता",
    "ರಸ್ತೆ",
    "சாலை",
    "రోడ్",
    "রোড",
)

CONF_CUED = 0.85
CONF_BARE = 0.55  # short box, no cue: kept at the planner's sensitivity 0.5 floor, dropped below it


@dataclass(frozen=True, slots=True)
class PinHit:
    start: int
    end: int


def is_postal_circle(digits: str) -> bool:
    """True when the first two digits are an Indian postal circle (11-85, or 90-99 for APO)."""
    if len(digits) < 2 or not digits[:2].isdigit():
        return False
    circle = int(digits[:2])
    return 11 <= circle <= 85 or 90 <= circle <= 99


def find_pins(text: str) -> list[PinHit]:
    """Every 6-digit candidate with a valid postal circle (cue handling is the caller's job)."""
    out: list[PinHit] = []
    for m in PIN_RE.finditer(text):
        digits = m.group(0).replace(" ", "")
        if is_postal_circle(digits):
            out.append(PinHit(m.start(), m.end()))
    return out


def _cue_regex(cues: tuple[str, ...]) -> re.Pattern[str]:
    latin = [re.escape(c) for c in cues if c.isascii()]
    other = [re.escape(c) for c in cues if not c.isascii()]
    parts = [rf"(?<![a-z])(?:{'|'.join(latin)})(?![a-z])"] + other
    return re.compile("|".join(parts))


_PIN_CUE_RE = _cue_regex(PIN_CUES)
_ADDRESS_CUE_RE = _cue_regex(ADDRESS_CUES)


def has_pin_cue(text: str) -> bool:
    """A strong PIN cue or an address word anywhere in text."""
    low = text.casefold()
    return bool(_PIN_CUE_RE.search(low) or _ADDRESS_CUE_RE.search(low))


__all__ = [
    "ADDRESS_CUES",
    "CONF_BARE",
    "CONF_CUED",
    "PIN_CUES",
    "PIN_RE",
    "PinHit",
    "find_pins",
    "has_pin_cue",
    "is_postal_circle",
]
