"""OCR text normalisation: Indic digits to ASCII, script labels, positional confusion repair.

Everything here works on text that lives for one frame only (never log it or store it).
"""

from __future__ import annotations

import unicodedata

# (first, last) code points of each script's block, in the labels used by ``OcrResult.scripts``
_SCRIPT_BLOCKS: tuple[tuple[str, int, int], ...] = (
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
)

# OCR confusions repaired only inside digit-expected fields (spec 5.2 step 4).
_DIGIT_CONFUSIONS = str.maketrans(
    {"O": "0", "o": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "B": "8", "S": "5"}
)


def to_ascii_digits(text: str) -> str:
    """Replace every Unicode decimal digit (Devanagari, Bengali, Tamil, Telugu, Kannada, ...) by ASCII."""
    out = []
    for ch in text:
        if ch.isascii():
            out.append(ch)
            continue
        value = unicodedata.decimal(ch, None)
        out.append(str(value) if value is not None else ch)
    return "".join(out)


def repair_digit_field(text: str) -> str:
    """Map O/o/Q to 0, I/l/| to 1, B to 8, S to 5. Call it only where digits are expected."""
    return text.translate(_DIGIT_CONFUSIONS)


def digit_ratio(text: str) -> float:
    """Share of non-space characters that are ASCII digits or one of the repairable confusions."""
    body = [c for c in text if not c.isspace()]
    if not body:
        return 0.0
    return sum(1 for c in body if c.isdigit() or c in "OoQIl|BS") / len(body)


def script_of(text: str) -> str:
    """Dominant script label of ``text``: an Indic block, ``latin`` or ``unknown`` (empty/other)."""
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for name, lo, hi in _SCRIPT_BLOCKS:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
        else:
            if ch.isascii() and ch.isalnum():
                counts["latin"] = counts.get("latin", 0) + 1
    if not counts:
        return "unknown"
    indic = {k: v for k, v in counts.items() if k != "latin"}
    if indic:
        return max(indic, key=lambda k: indic[k])
    return "latin"


def normalise_text(text: str) -> str:
    """Indic digits to ASCII and compatibility folding (full-width digits and letters)."""
    return to_ascii_digits(unicodedata.normalize("NFKC", text))
