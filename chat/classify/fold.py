"""Character-level cleanup shared by every text view, and the windowing of very long messages.

Everything an evader can do at character level is undone here, so the rest of the cascade sees plain text:

* control and invisible characters (NUL, C0/C1, zero-width, soft hyphen, bidi marks, variation selectors, tag
  characters, lone surrogates, combining marks stacked on letters) are removed;
* a right-to-left override (U+202E) shows the characters after it in reverse, so the DISPLAYED order is what is read;
* look-alike letters (Cyrillic, Greek, small capitals, regional-indicator letters) become the Latin letter, and
  digits of other scripts become ASCII digits. Fullwidth, circled and mathematical letters are handled by NFKC.

``windows`` splits a very long message into bounded slices (head, tail and evenly spread middle) so the work is capped
but no part of a message that fits in a slice is ever skipped silently.

Nothing here logs or stores text.
"""

from __future__ import annotations

import re

# Invisible and control characters. Tab, newline, VT, FF and CR are left to the whitespace collapse.
_INVISIBLE = re.compile("[\x00-\x08\x0e-\x1f\x7f-\x9f­͏؜ᅟᅠ឴឵᠋-᠏​-‏‪-‭⁠-⁯ㅤ︀-️﻿ﾠ￹-￻\ud800-\udfff\U000e0000-\U000e01ef]")
# Combining diacritical marks (zalgo). They carry no meaning for the Latin or Indic text we match.
_COMBINING = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")
_RLO = re.compile("‮([^‬]*)(?:‬|$)")
_WS = re.compile(r"\s+")


def _table() -> dict[int, str]:
    pairs: dict[str, str] = {}
    cyr = {
        "а": "a", "А": "A", "в": "b", "В": "B", "е": "e", "Е": "E", "і": "i", "І": "I", "ј": "j", "Ј": "J",
        "к": "k", "К": "K", "м": "m", "М": "M", "н": "h", "Н": "H", "о": "o", "О": "O", "р": "p", "Р": "P",
        "с": "c", "С": "C", "т": "t", "Т": "T", "у": "y", "У": "Y", "х": "x", "Х": "X", "ѕ": "s", "Ѕ": "S",
        "ԁ": "d", "һ": "h", "п": "n", "ɡ": "g", "ԛ": "q", "ԝ": "w", "ѵ": "v", "ү": "y", "ҽ": "e",
    }  # fmt: skip
    greek = {
        "α": "a", "Α": "A", "β": "b", "Β": "B", "γ": "y", "ε": "e", "Ε": "E", "ζ": "Z", "Ζ": "Z", "η": "n",
        "Η": "H", "ι": "i", "Ι": "I", "κ": "k", "Κ": "K", "μ": "m", "Μ": "M", "ν": "v", "Ν": "N", "ο": "o",
        "Ο": "O", "ρ": "p", "Ρ": "P", "τ": "t", "Τ": "T", "υ": "u", "Υ": "Y", "χ": "x", "Χ": "X", "ω": "w",
        "ϲ": "c", "ς": "c",
    }  # fmt: skip
    small_caps = dict(zip("ᴀʙᴄᴅᴇɢʜɪᴊᴋʟᴍɴᴏᴘʀᴛᴜᴠᴡʏᴢ", "abcdeghijklmnoprtuvwyz", strict=True))
    small_caps |= {"ғ": "f", "ǫ": "q", "ꜱ": "s", "ı": "i", "ȷ": "j", "ɑ": "a", "ɩ": "i", "ʟ": "l"}
    pairs.update(cyr)
    pairs.update(greek)
    pairs.update(small_caps)
    pairs["。"] = "."
    pairs["｡"] = "."
    for i in range(26):  # regional indicator symbols
        pairs[chr(0x1F1E6 + i)] = chr(ord("a") + i)
    for base in (0x660, 0x6F0, 0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66):
        for i in range(10):
            pairs[chr(base + i)] = str(i)
    return {ord(k): v for k, v in pairs.items()}


CONFUSABLES = _table()


def unrtl(text: str) -> str:
    """The text as displayed: the part after U+202E is shown reversed."""
    if "‮" not in text:
        return text
    return _RLO.sub(lambda m: m.group(1)[::-1], text)


def clean_chars(text: str) -> str:
    """NFKC-folded text with invisibles removed, display order restored and look-alikes mapped to Latin/ASCII."""
    import unicodedata

    t = unicodedata.normalize("NFKC", text)
    t = unrtl(t)
    t = _INVISIBLE.sub("", t)
    t = _COMBINING.sub("", t)
    return t.translate(CONFUSABLES)


# --- long messages -----------------------------------------------------------------------------------

WINDOW = 2000
MAX_WINDOWS = 8
_OVERLAP = 120


def squeeze_whitespace(text: str) -> str:
    """Invisible characters removed and every whitespace run collapsed to one space (cheap, regex only)."""
    return _WS.sub(" ", _INVISIBLE.sub("", text)).strip()


def windows(text: str) -> list[str]:
    """Slices of ``text`` to classify: the whole text when short, else head, tail and evenly spread middle slices.

    Work is capped at ``MAX_WINDOWS * WINDOW`` characters, whatever the input length. Neighbouring slices overlap
    so a word on a boundary is not cut.
    """
    t = squeeze_whitespace(text)
    n = len(t)
    if n <= WINDOW:
        return [t]
    starts = {0, n - WINDOW}
    middle = MAX_WINDOWS - 2
    span = n - 2 * WINDOW
    if span > 0:
        step = max(WINDOW - _OVERLAP, span // (middle + 1) or 1)
        pos = WINDOW - _OVERLAP
        while pos < n - WINDOW and len(starts) < MAX_WINDOWS:
            starts.add(pos)
            pos += step
    out = [t[s : s + WINDOW] for s in sorted(starts)]
    # Digest of the distinct words in order of first appearance: repeated padding collapses, so a word hidden anywhere
    # in a long, repetitive message still reaches the classifier.
    seen: dict[str, None] = {}
    total = 0
    for tok in t.split(" "):
        if tok not in seen:
            seen[tok] = None
            total += len(tok) + 1
            if total > 2 * WINDOW:
                break
    digest = " ".join(seen)[: 2 * WINDOW]
    if digest and digest not in out:
        out.append(digest)
    return out
