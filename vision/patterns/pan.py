"""PAN (Permanent Account Number) pattern ``[A-Z]{5}[0-9]{4}[A-Z]`` with OCR-confusion repair."""

from __future__ import annotations

import re
from dataclasses import dataclass

PAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Z0-9])")
# 4th character = holder type (spec 5.6): these raise the confidence to 0.9.
HOLDER_TYPES = frozenset("ABCFGHLJPTK")
CONF_KNOWN_HOLDER = 0.9
CONF_OTHER_HOLDER = 0.6

_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G", "4": "A", "7": "T"}
_TO_DIGIT = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
    "T": "7",
}


@dataclass(frozen=True, slots=True)
class PanHit:
    start: int
    end: int
    confidence: float
    repaired: bool  # True when OCR confusions were fixed by position


def _conf(pan: str, repaired: bool) -> float:
    base = CONF_KNOWN_HOLDER if pan[3] in HOLDER_TYPES else CONF_OTHER_HOLDER
    return base - 0.1 if repaired else base


def repair_pan(token: str) -> str | None:
    """Fix OCR confusions by position (letters in 0-4 and 9, digits in 5-8). None if not repairable."""
    if len(token) != 10 or not token.isascii() or not token.isalnum():
        return None
    out = []
    for i, ch in enumerate(token.upper()):
        if i < 5 or i == 9:
            ch = _TO_LETTER.get(ch, ch)
            if not ch.isalpha():
                return None
        else:
            ch = _TO_DIGIT.get(ch, ch)
            if not ch.isdigit():
                return None
        out.append(ch)
    return "".join(out)


def find_pan(text: str) -> list[PanHit]:
    """PAN matches in ``text`` (upper-cased first). Exact matches first; a bare 10-char alphanumeric
    token that matches only after positional repair is returned with ``repaired=True``."""
    upper = text.upper()
    hits = [PanHit(m.start(), m.end(), _conf(m.group(0), False), False) for m in PAN_RE.finditer(upper)]
    if hits:
        return hits
    for m in re.finditer(r"(?<![A-Z0-9])[A-Z0-9]{10}(?![A-Z0-9])", upper):
        fixed = repair_pan(m.group(0))
        if fixed is not None and PAN_RE.fullmatch(fixed):
            hits.append(PanHit(m.start(), m.end(), _conf(fixed, True), True))
    return hits
