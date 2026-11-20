"""Aadhaar number extraction from OCR text: 12 digits, Verhoeff-valid, optional 4-4-4 grouping.

The text is normalised first (Indic digits to ASCII, see ``vision.ocr.normalize``). A leading 0 or 1 is
accepted because SPECIMEN numbers start with 0 or 1; real numbers start with 2-9, and accepting both
only widens the mask (the safe side).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .verhoeff import is_valid

_RUN = re.compile(r"(?<!\d)(\d{4})[ \-]?(\d{4})[ \-]?(\d{4})(?!\d)")
_MASKED = re.compile(r"(?<![A-Za-z0-9])[Xx*]{4}[ \-]?[Xx*]{4}[ \-]?\d{4}(?!\d)")


@dataclass(frozen=True, slots=True)
class AadhaarHit:
    start: int
    end: int
    valid: bool  # Verhoeff passes


def _is_part_of_longer_number(text: str, start: int, end: int) -> bool:
    """A 12-digit match glued to more digit groups (a 16-digit card number) is not an Aadhaar."""
    after = text[end : end + 2]
    if after[:1] in (" ", "-") and after[1:2].isdigit():
        return True
    before = text[max(0, start - 2) : start]
    return len(before) == 2 and before[0].isdigit() and before[1] in (" ", "-")


def digit_run_hits(text: str) -> list[AadhaarHit]:
    """Every 12-digit run (grouped or not) in ``text``, with its Verhoeff verdict."""
    out = []
    for m in _RUN.finditer(text):
        if _is_part_of_longer_number(text, m.start(), m.end()):
            continue
        digits = "".join(m.groups())
        out.append(AadhaarHit(m.start(), m.end(), is_valid(digits)))
    return out


def find_aadhaar(text: str) -> list[AadhaarHit]:
    """Verhoeff-valid 12-digit runs only."""
    return [h for h in digit_run_hits(text) if h.valid]


def is_aadhaar_number(digits: str) -> bool:
    d = digits.replace(" ", "").replace("-", "")
    return len(d) == 12 and d.isascii() and d.isdigit() and is_valid(d)


def find_masked_aadhaar_hint(text: str) -> bool:
    """``XXXX XXXX 1234`` style masked number: a hint that the card is the already-masked variant."""
    return _MASKED.search(text) is not None
