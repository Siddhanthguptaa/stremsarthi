"""Language and script helpers for captions (docs/specs/audio.md 5.9). Pure functions, no I/O.

Caption language codes are the short codes of the contracts (``hi``, ``kn``, ``ta``, ``te``, ``bn``, ``en``,
plus the other Indian languages Sarvam handles). ``plan_conversion`` decides, from the source language, the
script of the text and the streamer's target language, whether the text needs translating, only transliterating
(romanised Hinglish to Devanagari) or nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ranges of the Unicode blocks of the scripts we caption in
_BLOCKS: tuple[tuple[int, int, str], ...] = (
    (0x0900, 0x097F, "devanagari"),
    (0x0980, 0x09FF, "bengali"),
    (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0B00, 0x0B7F, "odia"),
    (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x0600, 0x06FF, "arabic"),
)

# the language a native-script text is assumed to be in when the provider did not say
SCRIPT_LANG: dict[str, str] = {
    "devanagari": "hi",
    "bengali": "bn",
    "gurmukhi": "pa",
    "gujarati": "gu",
    "odia": "or",
    "tamil": "ta",
    "telugu": "te",
    "kannada": "kn",
    "malayalam": "ml",
    "arabic": "ur",
}

# the script each language is written in
LANG_SCRIPT: dict[str, str] = {
    "hi": "devanagari",
    "mr": "devanagari",
    "ne": "devanagari",
    "bn": "bengali",
    "as": "bengali",
    "pa": "gurmukhi",
    "gu": "gujarati",
    "or": "odia",
    "od": "odia",
    "ta": "tamil",
    "te": "telugu",
    "kn": "kannada",
    "ml": "malayalam",
    "ur": "arabic",
    "en": "latin",
}

# Sarvam translate/transliterate language codes. MAYURA is the 11-language code-mixed model (also accepts
# ``auto`` as source); the rest need sarvam-translate (22 languages, explicit source).
MAYURA_LANGS = frozenset({"bn", "en", "gu", "hi", "kn", "ml", "mr", "or", "pa", "ta", "te"})
SARVAM_CODES: dict[str, str] = {
    **{c: f"{c}-IN" for c in ("bn", "en", "gu", "hi", "kn", "ml", "mr", "pa", "ta", "te")},
    "or": "od-IN",
    "od": "od-IN",
    "as": "as-IN",
    "ne": "ne-IN",
    "ur": "ur-IN",
    "sa": "sa-IN",
    "sd": "sd-IN",
    "mai": "mai-IN",
    "kok": "kok-IN",
    "doi": "doi-IN",
    "brx": "brx-IN",
    "ks": "ks-IN",
    "mni": "mni-IN",
    "sat": "sat-IN",
}

# IndicTrans2 FLORES codes
IT2_CODES: dict[str, str] = {
    "hi": "hin_Deva",
    "en": "eng_Latn",
    "kn": "kan_Knda",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "bn": "ben_Beng",
    "mr": "mar_Deva",
    "gu": "guj_Gujr",
    "ml": "mal_Mlym",
    "pa": "pan_Guru",
    "or": "ory_Orya",
    "od": "ory_Orya",
    "ur": "urd_Arab",
    "as": "asm_Beng",
    "ne": "npi_Deva",
}

Action = Literal["none", "transliterate", "translate"]


def script_of_char(ch: str) -> str | None:
    cp = ord(ch)
    for lo, hi, name in _BLOCKS:
        if lo <= cp <= hi:
            return name
    if ch.isascii() and ch.isalpha():
        return "latin"
    return None


def dominant_script(text: str) -> str | None:
    """The script most letters of ``text`` are written in (None when it has no letters, e.g. only masks)."""
    counts: dict[str, int] = {}
    for ch in text:
        s = script_of_char(ch)
        if s is not None:
            counts[s] = counts.get(s, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def normalise_code(code: str | None) -> str:
    """'hi-IN' -> 'hi', 'Hindi' -> 'hi'; unknown or empty -> 'unknown'. Always a valid contract code."""
    if not code:
        return "unknown"
    c = code.strip().lower().replace("_", "-")
    names = {"hindi": "hi", "english": "en", "kannada": "kn", "tamil": "ta", "telugu": "te", "bengali": "bn"}
    if c in names:
        return names[c]
    c = c.split("-")[0]
    return c if c.isalnum() and 1 <= len(c) <= 8 else "unknown"


def source_language(lang: str, text: str) -> str:
    """The language of ``text``: the provider's tag when it is usable, else guessed from the script."""
    code = normalise_code(lang)
    if code != "unknown":
        return code
    script = dominant_script(text)
    if script is None:
        return "unknown"
    return SCRIPT_LANG.get(script, "en")


@dataclass(frozen=True, slots=True)
class Plan:
    action: Action
    src: str  # language code of the text as it will be sent ("auto" only when the language is unknown)
    tgt: str
    out_lang: str  # language the resulting caption is in when ``action`` is not degraded


def plan_conversion(src_lang: str, text: str, tgt: str) -> Plan:
    """Decide what to do with ``text`` (source tag ``src_lang``) for target language ``tgt``.

    * text already in the target language (and its script): nothing, no API call;
    * romanised text tagged as the target language (Hinglish for ``hi``): transliteration only;
    * everything else: translation. A language tag that contradicts the script (``en`` on Devanagari text)
      is overruled by the script; an unknown tag on Latin text becomes ``auto``.
    """
    tgt = normalise_code(tgt)
    script = dominant_script(text)
    known = normalise_code(src_lang) != "unknown"
    src = source_language(src_lang, text)
    if script is None:  # nothing but masks: nothing to convert
        return Plan("none", src, tgt, src if src != "unknown" else tgt)
    if script != "latin" and LANG_SCRIPT.get(src) != script:
        src = SCRIPT_LANG.get(script, src)
    elif script == "latin" and not known:
        src = "auto"  # could be English or romanised Hindi: let the provider decide
    tgt_script = LANG_SCRIPT.get(tgt)
    if src == tgt:
        if script == "latin" and tgt_script not in (None, "latin"):
            return Plan("transliterate", "en", tgt, tgt)  # romanised Hindi shown in Devanagari
        return Plan("none", src, tgt, tgt)
    return Plan("translate", src, tgt, tgt)
