"""Text normalisation for the classifier cascade (docs/specs/chat.md 5.1 step 2).

``normalize`` returns several views of one message:

* ``raw``       the unmodified text (never altered, only used for identity checks),
* ``link_text`` NFKC with zero-width characters removed, case kept: for URL / UPI / phone regexes,
* ``text``      the lexicon view: NFKC, lowercase, zero-width and Latin combining marks removed,
                homoglyphs and leet mapped, letter runs collapsed, single-letter separators joined,
* ``roman``     a rough Latin transliteration of the Devanagari part (empty when there is none),
* ``glued``     the lexicon view with in-word ``_ . - '`` separators removed (``ch_akke``), empty when equal.

Links never reach the lexicon views (a domain such as ``bbc.com`` is not a word), and ``link_text`` has the
usual link and UPI-ID obfuscation undone (``hxxp``, ``[.]``, ``(at)``, ``at the rate``).

Nothing here logs or stores text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .fold import clean_chars
from .links import blank_urls, refang

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_LEET_CHARS = frozenset("013457@$")

# Token characters for lexicon matching: Latin letters/digits and every Indic block except the
# danda/double danda and abbreviation sign (which separate words).
TOKEN_RE = re.compile(r"[a-z0-9*ऀ-ॣ०-९ॱ-ൿ]+")

_RUNS = re.compile(r"([a-z])\1{2,}")
_SEPS = r"[\s.\-_*|/,+]{1,3}"
_SING = "[a-z013457@$]"  # a letter, or a digit or symbol that stands for one (b h 3 n c h 0 d)
_SINGLES = re.compile(rf"(?<![a-z0-9@$])[a-z](?:{_SEPS}{_SING}(?![a-z0-9]))+(?![a-z0-9])")
_SEP_STRIP = re.compile(_SEPS)
# Indic text spelled one code point at a time (consonants, vowel signs, viramas): "च.ू.त.ि.य.ा"
_INDIC = "[ऀ-෿]"
_INDIC_SINGLES = re.compile(rf"(?<!{_INDIC}){_INDIC}(?:{_SEPS}{_INDIC})+(?!{_INDIC})")
_SPACES = re.compile(r"\s+")


_VOWEL_DIGRAPHS = (("oo", "u"), ("ee", "i"), ("uu", "u"), ("ii", "i"))
_H_AFTER = re.compile(r"(?<=[bcdgjk])h")
_FINAL_VOWEL = re.compile(r"[aeio]$")


def fold_token(token: str) -> str:
    """Phonetic key of a Latin Hinglish word, so spelling variants meet (``chootiya``/``chutiya``/``chutia``).

    Non-ASCII tokens are returned as they are. Applied to lexicon terms and message tokens alike.
    """
    if not token.isascii():
        return token
    t = token
    for a, b in _VOWEL_DIGRAPHS:
        if a in t:
            t = t.replace(a, b)
    t = squeeze(t)
    if "h" in t:
        t = _H_AFTER.sub("", t)
    for a, b in (("ph", "f"), ("ck", "k"), ("q", "k"), ("w", "v"), ("z", "j"), ("y", "i")):
        if a in t:
            t = t.replace(a, b)
    t = squeeze(t)
    if len(t) >= 5:
        t = _FINAL_VOWEL.sub("a", t)
    return t


def squeeze(token: str) -> str:
    """Collapse every repeated letter to one (``chutiyaaa`` -> ``chutiya``, ``kutta`` -> ``kuta``)."""
    out: list[str] = []
    for ch in token:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _leet_tokens(text: str) -> str:
    """Leet only inside tokens that already contain a letter, so numbers and ``@mentions`` stay intact."""
    if not any(ch in _LEET_CHARS for ch in text):
        return text
    parts = text.split(" ")
    for i, tok in enumerate(parts):
        if len(tok) < 2 or not any(ch in _LEET_CHARS for ch in tok):
            continue
        body = tok[1:] if tok[0] in "@$" else tok  # a leading @ is a mention, not an 'a'
        if sum(ch.isalpha() for ch in body) < 1:
            continue
        # a purely numeric-ish token with one letter ("2024x") is left alone
        if sum(ch.isalpha() for ch in body) == 1 and sum(ch.isdigit() for ch in body) >= 3:
            continue
        parts[i] = tok[: len(tok) - len(body)] + body.translate(_LEET)
    return " ".join(parts)


def _strip_latin_marks(text: str) -> str:
    """Remove combining marks stacked on ASCII letters (zalgo) and flatten accented Latin letters."""
    if text.isascii():
        return text
    out: list[str] = []
    for ch in text:
        if ch.isascii():
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat == "Mn" and out and out[-1].isascii() and out[-1].isalpha():
            continue
        if cat.startswith("L") and unicodedata.name(ch, "").startswith("LATIN"):
            base = unicodedata.normalize("NFKD", ch)
            out.append("".join(c for c in base if c.isascii()) or ch)
            continue
        out.append(ch)
    return "".join(out)


# --- Devanagari -------------------------------------------------------------------------------

_DEV_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh",
    "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d",
    "ध": "dh", "न": "n", "प": "p", "फ": "f", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r",
    "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
}  # fmt: skip
_DEV_MATRA = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
    "ौ": "au", "ॅ": "e", "ॉ": "o", "ॆ": "e", "ॊ": "o",
}  # fmt: skip
_DEV_VOWEL = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai",
    "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e",
}  # fmt: skip
_DEV_DIGITS = {chr(0x966 + i): str(i) for i in range(10)}
_VIRAMA = "्"
_NUKTA = "़"
_ANUSVARA = ("ं", "ँ")
_DEV_RE = re.compile("[ऀ-ॿ]")


def has_devanagari(text: str) -> bool:
    return _DEV_RE.search(text) is not None


def _native_clean(text: str) -> str:
    """Native-script cleanup so lexicon and text agree: strip nukta, chandrabindu -> anusvara."""
    if not _DEV_RE.search(text):
        return text
    return text.replace(_NUKTA, "").replace("ँ", "ं")


def _translit_word(word: str) -> str:
    """Rough Devanagari -> Latin for one word, with right-to-left schwa deletion (``bhosadike`` -> ``bhosdike``)."""
    syll: list[list[object]] = []  # [onset, vowel, deletable]
    i = 0
    n = len(word)
    while i < n:
        ch = word[i]
        if ch in _DEV_CONS:
            onset = _DEV_CONS[ch]
            i += 1
            # conjunct chain: consonant + virama + consonant ...
            while i + 1 < n and word[i] == _VIRAMA and word[i + 1] in _DEV_CONS:
                onset += _DEV_CONS[word[i + 1]]
                i += 2
            if i < n and word[i] == _VIRAMA:  # trailing virama: no vowel
                syll.append([onset, "", False])
                i += 1
            elif i < n and word[i] in _DEV_MATRA:
                syll.append([onset, _DEV_MATRA[word[i]], False])
                i += 1
            else:
                syll.append([onset, "a", True])  # inherent schwa
        elif ch in _DEV_VOWEL:
            syll.append(["", _DEV_VOWEL[ch], False])
            i += 1
        elif ch in _ANUSVARA:
            syll.append(["n", "", False])
            i += 1
        elif ch == "ः":
            syll.append(["h", "", False])
            i += 1
        elif ch in _DEV_DIGITS:
            syll.append([_DEV_DIGITS[ch], "", False])
            i += 1
        else:
            i += 1
    # schwa deletion, right to left
    for idx in range(len(syll) - 1, -1, -1):
        onset, vowel, deletable = syll[idx]
        if not deletable:
            continue
        if idx == len(syll) - 1:
            syll[idx][1] = ""
            continue
        if idx == 0:
            continue
        prev_has_vowel = bool(syll[idx - 1][1])
        next_has_vowel = bool(syll[idx + 1][1])
        if prev_has_vowel and next_has_vowel:
            syll[idx][1] = ""
    return "".join(f"{o}{v}" for o, v, _ in syll)


_DEV_WORD = re.compile("[ऀ-ॿ]+")


def transliterate_devanagari(text: str) -> str:
    """Roman form of the Devanagari words in ``text`` (other characters are kept)."""
    return _DEV_WORD.sub(lambda m: _translit_word(m.group(0)), text)


@dataclass(frozen=True, slots=True)
class Normalized:
    raw: str
    link_text: str
    text: str
    roman: str
    glued: str = ""
    fanged: bool = False  # the link view differs from the text as written: obfuscation was undone
    blobs: tuple[str, ...] = ()  # runs of single letters joined into one word (``b.h.e.n.c.h.o.d``)


def link_view(text: str) -> str:
    """Case-preserving NFKC view without zero-width characters (for URL and UPI regexes), links refanged."""
    return refang(_strip_latin_marks(clean_chars(text)))


def lexicon_view_ex(text: str) -> tuple[str, list[str]]:
    """The lexicon form of ``text`` and the runs of single letters that were joined (``b h e n c h o d``)."""
    t = _strip_latin_marks(clean_chars(text)).lower()
    t = _native_clean(t)
    t = _symbol_leet(_SPACES.sub(" ", t).strip())
    t = _leet_tokens(t)
    t = _RUNS.sub(r"\1\1", t)
    joined: list[str] = []
    if len(t) > 2 and any(ch in " .-_*|/,+" for ch in t):

        def _join(m: re.Match[str]) -> str:
            out = _SEP_STRIP.sub("", m.group(0))
            joined.append(out)
            return out

        t = _SINGLES.sub(_join, t)
        t = _INDIC_SINGLES.sub(_join, t)
        if joined:
            t = _leet_tokens(t)
    return t, joined


def lexicon_view(text: str) -> str:
    """The lexicon form of ``text``. Used for both messages and lexicon terms so they always agree."""
    return lexicon_view_ex(text)[0]


_SYMBOL_LEET = {"!": "i", "|": "l", "(": "c", "[": "c"}
_SYMBOL_INNER = re.compile(r"(?<=[a-z0-9])[!|(\[](?=[a-z0-9])")
_SYMBOL_LEAD = re.compile(r"(?<![a-z0-9!|(\[])[(|\[](?=[a-z]{2})")


def _symbol_leet(text: str) -> str:
    """``ch!ut!ya``, ``(hutiya``, ``|und``: symbols that stand for a letter, only inside or at the start of a word."""
    if not any(c in text for c in "!|(["):
        return text
    text = _SYMBOL_INNER.sub(lambda m: _SYMBOL_LEET[m.group(0)], text)
    return _SYMBOL_LEAD.sub(lambda m: _SYMBOL_LEET[m.group(0)], text)


_INWORD = re.compile(r"(?<=[a-z])[_.\-'`·•](?=[a-z])")


def normalize(text: str) -> Normalized:
    link = link_view(text)
    fanged = link != _strip_latin_marks(clean_chars(text))
    lex, blobs = lexicon_view_ex(blank_urls(link))
    roman = transliterate_devanagari(lex) if has_devanagari(lex) else ""
    glued = _INWORD.sub("", lex) if any(c in lex for c in "_.-'`·•") else ""
    return Normalized(
        raw=text,
        link_text=link,
        text=lex,
        roman=roman,
        glued=glued if glued != lex else "",
        fanged=fanged,
        blobs=tuple(b for b in blobs if len(b) >= 5),
    )
