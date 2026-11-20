"""Spoken number words per language, written independently of ``audio/pii/data`` (own spellings and variants).

Each token has two forms: ``stt`` is how a recogniser would write the word (native script or Latin, sometimes
an Arabic digit) and ``tts`` is the respelling the offline English TTS needs to say something close to it.
"""

from __future__ import annotations

import random
from typing import NamedTuple

LANGS = ("hi_latn", "hi_deva", "en", "kn", "ta", "te", "bn")
NATIVE_SCRIPT = {"hi_deva": "Devanagari", "kn": "Kannada", "ta": "Tamil", "te": "Telugu", "bn": "Bengali"}


class Tok(NamedTuple):
    stt: str
    tts: str
    digit: bool = False


def _v(*pairs: str) -> list[tuple[str, str]]:
    """``"stt|tts"`` strings (or a bare string when both are the same) to pairs."""
    out = []
    for p in pairs:
        a, _, b = p.partition("|")
        out.append((a, b or a))
    return out


DIGITS: dict[str, list[list[tuple[str, str]]]] = {
    "hi_latn": [
        _v("shunya", "zero", "sifar"),
        _v("ek"),
        _v("do"),
        _v("teen"),
        _v("chaar", "char"),
        _v("paanch", "panch"),
        _v("chhe|cheh", "chhah|cheh", "cheh"),
        _v("saat", "sat"),
        _v("aath", "ath"),
        _v("nau"),
    ],
    "hi_deva": [
        _v("शून्य|shunya", "ज़ीरो|zero", "जीरो|zero"),
        _v("एक|ek"),
        _v("दो|do"),
        _v("तीन|teen"),
        _v("चार|chaar"),
        _v("पांच|paanch", "पाँच|paanch"),
        _v("छह|cheh", "छे|cheh", "छः|cheh"),
        _v("सात|saat"),
        _v("आठ|aath"),
        _v("नौ|nau"),
    ],
    "en": [
        _v("zero", "oh"),
        _v("one"),
        _v("two"),
        _v("three"),
        _v("four"),
        _v("five"),
        _v("six"),
        _v("seven"),
        _v("eight"),
        _v("nine"),
    ],
    "kn": [
        _v("ಸೊನ್ನೆ|sonne"),
        _v("ಒಂದು|ondu"),
        _v("ಎರಡು|eradu"),
        _v("ಮೂರು|mooru"),
        _v("ನಾಲ್ಕು|naalku"),
        _v("ಐದು|aidu"),
        _v("ಆರು|aaru"),
        _v("ಏಳು|elu"),
        _v("ಎಂಟು|entu"),
        _v("ಒಂಬತ್ತು|ombattu"),
    ],
    "ta": [
        _v("பூஜ்யம்|poojyam"),
        _v("ஒன்று|ondru"),
        _v("இரண்டு|irandu"),
        _v("மூன்று|moondru"),
        _v("நான்கு|naangu"),
        _v("ஐந்து|ainthu"),
        _v("ஆறு|aaru"),
        _v("ஏழு|ezhu"),
        _v("எட்டு|ettu"),
        _v("ஒன்பது|onbathu"),
    ],
    "te": [
        _v("సున్నా|sunna"),
        _v("ఒకటి|okati"),
        _v("రెండు|rendu"),
        _v("మూడు|moodu"),
        _v("నాలుగు|nalugu"),
        _v("ఐదు|aidu"),
        _v("ఆరు|aaru"),
        _v("ఏడు|edu"),
        _v("ఎనిమిది|enimidi"),
        _v("తొమ్మిది|tommidi"),
    ],
    "bn": [
        _v("শূন্য|shunyo"),
        _v("এক|ek"),
        _v("দুই|dui"),
        _v("তিন|tin"),
        _v("চার|char"),
        _v("পাঁচ|panch"),
        _v("ছয়|choy"),
        _v("সাত|shat"),
        _v("আট|aat"),
        _v("নয়|noy"),
    ],
}
REPEAT = {
    "hi_latn": [("double", "double"), ("dabal", "double")],
    "hi_deva": [("डबल", "double")],
    "en": [("double", "double")],
    "kn": [("double", "double")],
    "ta": [("double", "double")],
    "te": [("double", "double")],
    "bn": [("double", "double")],
}
TRIPLE = {
    "hi_latn": [("triple", "triple"), ("tripal", "triple")],
    "hi_deva": [("ट्रिपल", "triple")],
}
LETTERS = {
    "a": "ay",
    "b": "bee",
    "c": "see",
    "d": "dee",
    "e": "ee",
    "f": "eff",
    "x": "ex",
    "z": "zed",
}
HANDLE_TTS = {
    "oksbi": "ok ess bee eye",
    "okhdfcbank": "ok aitch dee eff see bank",
    "okaxis": "ok axis",
    "okicici": "ok icici",
    "ybl": "why bee el",
    "paytm": "pay tee em",
    "ibl": "eye bee el",
    "apl": "ay pee el",
    "specimen": "specimen",
}


def digit_tok(d: str, lang: str, rng: random.Random, p_arabic: float = 0.12) -> Tok:
    """One spoken digit: a number word of the language (or, sometimes, the Arabic digit a recogniser prints)."""
    stt, tts = rng.choice(DIGITS[lang][int(d)])
    if rng.random() < p_arabic:
        stt = d
    return Tok(stt, tts, True)


def spell_digits(
    digits: str, lang: str, rng: random.Random, *, p_repeat: float = 0.4, p_arabic: float = 0.12
) -> list[Tok]:
    """Digit string to spoken tokens, using 'double'/'triple' for repeated digits with probability ``p_repeat``."""
    out: list[Tok] = []
    i = 0
    while i < len(digits):
        run = 1
        while i + run < len(digits) and digits[i + run] == digits[i] and run < 3:
            run += 1
        if run >= 2 and rng.random() < p_repeat:
            if run == 3 and lang in TRIPLE:
                stt, tts = rng.choice(TRIPLE[lang])
            else:
                run = 2
                stt, tts = rng.choice(REPEAT[lang])
            out.append(Tok(stt, tts, True))
            out.append(digit_tok(digits[i], lang, rng, p_arabic))
            i += run
        else:
            out.append(digit_tok(digits[i], lang, rng, p_arabic))
            i += 1
    return out


def spoken_upi(value: str, lang: str, rng: random.Random) -> list[Tok]:
    """A SPECIMEN UPI id spoken as a person would: name part, 'at', handle (spelled or whole)."""
    local, _, handle = value.partition("@")
    toks: list[Tok] = []
    if local.startswith("zz-specimen-"):
        toks.append(Tok("zz", "zed zed"))
        toks.append(Tok("specimen", "specimen"))
        for ch in local[len("zz-specimen-") :]:
            if ch.isdigit():
                toks.append(digit_tok(ch, lang, rng, p_arabic=0.3))
            else:
                toks.append(Tok(ch, LETTERS[ch]))
    else:  # a phone-number style id: digits only
        toks.extend(spell_digits(local, lang, rng, p_repeat=0.3))
    at = rng.choice((("at",), ("at", "the", "rate"), ("at", "the", "rate", "of")))
    toks.extend(Tok(w, w) for w in at)
    toks.append(Tok(handle, HANDLE_TTS.get(handle, handle)))
    return toks
