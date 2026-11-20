"""One labelled chat message and the helpers shared by the generators (obfuscation, picking)."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Any

LABELS = ("clean", "abuse", "scam", "fake_upi", "doxxing", "doxxing_staff")


@dataclass
class Msg:
    text: str
    label: str  # one of LABELS
    kind: str  # sub-type for per-slice reports
    lang: str = "hinglish"  # hinglish | deva | en | kn | ta | te | bn | mixed
    gate: bool = True  # False: reported by kind, left out of the gating rates (honestly ambiguous)
    staff: str = ""  # "" | "broadcaster" | "moderator"
    tags: list[str] = field(default_factory=list)  # e.g. ["obfuscated", "hard_negative"]
    id: str = ""
    author: str = ""
    platform: str = "youtube"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Msg:
        return Msg(**d)


def pick(rng: random.Random, seq: list[str] | tuple[str, ...]) -> str:
    return seq[rng.randrange(len(seq))]


# --- obfuscation (applied only to Latin text) ----------------------------------------------------------------------
LEET = {"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7"}
HOMOGLYPH = {"a": "а", "e": "е", "o": "о", "c": "с", "p": "р", "x": "х", "i": "і", "y": "у"}  # Cyrillic look-alikes
FULLWIDTH = {c: chr(ord(c) + 0xFEE0) for c in "abcdefghijklmnopqrstuvwxyz"}
ZW = "​"


def leet(word: str, rng: random.Random, p: float = 0.6) -> str:
    return "".join(LEET.get(c, c) if c in LEET and rng.random() < p else c for c in word)


def spaced(word: str, sep: str = " ") -> str:
    return sep.join(word)


def starred(word: str, rng: random.Random) -> str:
    idx = [i for i, c in enumerate(word) if c in "aeiou"]
    if not idx:
        return word
    i = rng.choice(idx)
    return word[:i] + "*" + word[i + 1 :]


def stretched(word: str, rng: random.Random) -> str:
    i = rng.randrange(len(word))
    return word[:i] + word[i] * rng.randint(2, 4) + word[i + 1 :]


def homoglyph(word: str, rng: random.Random) -> str:
    chars = list(word)
    idx = [i for i, c in enumerate(chars) if c in HOMOGLYPH]
    for i in rng.sample(idx, min(len(idx), rng.randint(1, 2))):
        chars[i] = HOMOGLYPH[chars[i]]
    return "".join(chars)


def zero_width(word: str, rng: random.Random) -> str:
    i = rng.randrange(1, max(2, len(word)))
    return word[:i] + ZW + word[i:]


def fullwidth(word: str) -> str:
    return "".join(FULLWIDTH.get(c, c) for c in word)


def hyphenated(word: str, rng: random.Random) -> str:
    i = rng.randrange(1, max(2, len(word)))
    return word[:i] + rng.choice(("-", ".", "_", "'")) + word[i:]


OBFUSCATORS = ("leet", "spaced", "dotted", "starred", "stretched", "homoglyph", "zero_width", "fullwidth", "hyphen", "upper")


def obfuscate(word: str, rng: random.Random, how: str | None = None) -> tuple[str, str]:
    how = how or rng.choice(OBFUSCATORS)
    if how == "leet":
        return leet(word, rng), how
    if how == "spaced":
        return spaced(word), how
    if how == "dotted":
        return spaced(word, "."), how
    if how == "starred":
        return starred(word, rng), how
    if how == "stretched":
        return stretched(word, rng), how
    if how == "homoglyph":
        return homoglyph(word, rng), how
    if how == "zero_width":
        return zero_width(word, rng), how
    if how == "fullwidth":
        return fullwidth(word), how
    if how == "hyphen":
        return hyphenated(word, rng), how
    return word.upper(), "upper"
