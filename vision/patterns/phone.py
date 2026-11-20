"""Indian mobile numbers in OCR text (text is already normalised: ASCII digits).

A mobile number is 10 digits starting 6-9, optionally prefixed ``+91``, ``91`` or ``0`` and written
contiguously or grouped (``98765 43210``, ``987-654-3210``, ``+91 98765-43210``). Matches inside a longer
digit run are ignored. A 12-digit run that passes Verhoeff and starts ``91`` is an Aadhaar number, not
``91`` + a phone (the card detector owns it).

Pure text: no models, no logging. Hits carry offsets and a confidence, never the digits elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .verhoeff import is_valid

_PREFIX = r"(?:(?:\+\s?91|\b91|0)[\s-]?)?"
_BODY = r"[6-9](?:\d{4}[\s-]?\d{5}|\d{2}[\s-]?\d{3}[\s-]?\d{4}|\d{3}[\s-]?\d{3}[\s-]?\d{3})"
PHONE_RE = re.compile(rf"(?<![\d+]){_PREFIX}(?P<num>{_BODY})(?![\d]|[\s-]\d)")

CONF_PLAIN = 0.85
CONF_PREFIXED = 0.93


@dataclass(frozen=True, slots=True)
class PhoneHit:
    start: int
    end: int
    confidence: float
    prefixed: bool


def find_phones(text: str) -> list[PhoneHit]:
    """Every Indian mobile number in ``text`` (non-overlapping, left to right)."""
    hits: list[PhoneHit] = []
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 12 and digits.startswith("91") and "+" not in m.group(0) and is_valid(digits):
            continue  # an Aadhaar number that happens to start 91
        prefixed = m.start("num") > m.start()
        hits.append(PhoneHit(m.start(), m.end(), CONF_PREFIXED if prefixed else CONF_PLAIN, prefixed))
    return hits


__all__ = ["CONF_PLAIN", "CONF_PREFIXED", "PHONE_RE", "PhoneHit", "find_phones"]
