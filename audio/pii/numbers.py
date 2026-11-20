"""Spoken-number handling (docs/specs/audio.md 5.5): normalisation, tokens, number words and number runs.

In-process only: tokens hold transcript text and must never be logged.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from contracts.speech import Word

DATA_FILE = Path(__file__).with_name("data") / "number_words.yaml"

_SCRIPT_DIGITS = (
    "०१२३४५६७८९"  # Devanagari
    "০১২৩৪৫৬৭৮৯"  # Bengali
    "௦௧௨௩௪௫௬௭௮௯"  # Tamil
    "౦౧౨౩౪౫౬౭౮౯"  # Telugu
    "೦೧೨೩೪೫೬೭೮೯"  # Kannada
    "٠١٢٣٤٥٦٧٨٩"  # Arabic-Indic
)
_DIGIT_MAP = {ord(ch): str(i % 10) for i, ch in enumerate(_SCRIPT_DIGITS)}
_DROP = {0x200B, 0x200C, 0x200D, 0xFEFF}  # zero-width characters ASR sometimes emits


def normalise_text(text: str) -> str:
    """NFKC, lowercase, script digits to ASCII, punctuation to spaces (Indic combining marks are kept)."""
    t = unicodedata.normalize("NFKC", text).lower()
    out: list[str] = []
    for ch in t:
        if ord(ch) in _DROP:
            continue
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out).translate(_DIGIT_MAP)


def norm_phrase(tokens: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for t in tokens:
        out.extend(normalise_text(str(t)).split())
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Vocab:
    digit_of: dict[str, str]
    compound: frozenset[str]
    repeat: dict[str, int]
    plus: frozenset[str]
    ninety: frozenset[str]
    at_strong: tuple[tuple[str, ...], ...]
    at_weak: tuple[tuple[str, ...], ...]
    handles: frozenset[str]
    cue_generic: tuple[tuple[str, ...], ...]
    cue_address_open: tuple[tuple[str, ...], ...]
    addr_strong: tuple[tuple[str, ...], ...]
    addr_weak: tuple[tuple[str, ...], ...]
    name_stop: frozenset[str]


@lru_cache(maxsize=1)
def load_vocab(path: Path | None = None) -> Vocab:
    raw: dict[str, Any] = yaml.safe_load((path or DATA_FILE).read_text(encoding="utf-8"))
    digit_of: dict[str, str] = {}
    for d, words in raw["digits"].items():
        for w in words:
            for tok in norm_phrase([w]):
                digit_of[tok] = str(d)

    def phrases(items: Sequence[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
        # longest first so "at the rate of" wins over "at"
        return tuple(sorted((norm_phrase(p) for p in items), key=len, reverse=True))

    def words(items: Sequence[str]) -> frozenset[str]:
        return frozenset(t for w in items for t in norm_phrase([w]))

    return Vocab(
        digit_of=digit_of,
        compound=words(raw["compound"]),
        repeat={t: int(n) for w, n in raw["repeat"].items() for t in norm_phrase([w])},
        plus=words(raw["plus"]),
        ninety=words(raw["ninety"]),
        at_strong=phrases(raw["at_markers"]["strong"]),
        at_weak=phrases(raw["at_markers"]["weak"]),
        handles=words(raw["handles"]),
        cue_generic=phrases(raw["cues"]["generic"]),
        cue_address_open=phrases(raw["cues"]["address_open"]),
        addr_strong=phrases(raw["address"]["strong"]),
        addr_weak=phrases(raw["address"]["weak"]),
        name_stop=words(raw["name_stop"]),
    )


# --------------------------------------------------------------------------- tokens
@dataclass(frozen=True, slots=True)
class Tok:
    text: str  # normalised, one token
    t0: float
    t1: float
    approx: bool
    seg_id: int
    widx0: int  # first source word (index into TranscriptWindow.words)
    widx1: int  # last source word


def tokenize(words: Sequence[Word]) -> list[Tok]:
    """Normalise each word into one or more tokens. ``@`` becomes the token ``at``."""
    out: list[Tok] = []
    for i, w in enumerate(words):
        parts = normalise_text(w.text.replace("@", " at ")).split()
        if not parts:
            continue
        step = (w.t1 - w.t0) / len(parts)
        for k, p in enumerate(parts):
            t0 = w.t0 + k * step
            t1 = w.t0 + (k + 1) * step if k < len(parts) - 1 else w.t1
            out.append(Tok(p, t0, t1, w.approx, w.seg_id, i, i))
    return out


def _single_letter(text: str) -> bool:
    return len(text) == 1 and text.isalpha() and text.isascii()


def join_single_letters(toks: Sequence[Tok], max_gap_s: float = 0.6) -> list[Tok]:
    """Join runs of two or more single-letter tokens: ``o k s b i`` becomes ``oksbi``."""
    out: list[Tok] = []
    i, n = 0, len(toks)
    while i < n:
        j = i
        if _single_letter(toks[i].text):
            while j + 1 < n and _single_letter(toks[j + 1].text) and toks[j + 1].t0 - toks[j].t1 < max_gap_s:
                j += 1
        if j > i:
            grp = toks[i : j + 1]
            out.append(
                Tok(
                    "".join(t.text for t in grp),
                    grp[0].t0,
                    grp[-1].t1,
                    any(t.approx for t in grp),
                    grp[0].seg_id,
                    grp[0].widx0,
                    grp[-1].widx1,
                )
            )
            i = j + 1
        else:
            out.append(toks[i])
            i += 1
    return out


def prepare(words: Sequence[Word]) -> list[Tok]:
    return join_single_letters(tokenize(words))


# --------------------------------------------------------------------------- number words
def digit_value(text: str, vocab: Vocab | None = None) -> str | None:
    """Digits a token stands for: ``'9'`` for ``nau``, ``'98765'`` for a numeral, else ``None``."""
    v = vocab or load_vocab()
    if text.isascii() and text.isdigit():
        return text
    return v.digit_of.get(text)


def to_digits(text: str, vocab: Vocab | None = None) -> str | None:
    """Expand a short spoken phrase (``double nine eight``, ``plus nine one``) to digits, or ``None``."""
    toks = [Tok(t, 0.0, 0.0, False, 0, 0, 0) for t in normalise_text(text).split()]
    runs = number_runs(toks, gap_s=1e9, vocab=vocab)
    return runs[0].digits if len(runs) == 1 and runs[0].tok0 <= 1 and runs[0].tok1 == len(toks) - 1 else None


@dataclass(frozen=True, slots=True)
class NumRun:
    digits: str  # expanded digits ("" if only compound words such as "sau")
    tok0: int  # index range into the token list
    tok1: int
    t0: float
    t1: float
    plus: bool  # preceded by "plus" (a country code follows)

    @property
    def n(self) -> int:
        return len(self.digits)


def number_runs(toks: Sequence[Tok], gap_s: float = 1.0, vocab: Vocab | None = None) -> list[NumRun]:
    """Maximal runs of number tokens whose gaps are below ``gap_s`` (a run continues across segments).

    ``double``/``triple`` repeat the next single digit; ``ninety one`` is read as 91; compound words
    (``sau``, ``hazaar``, ``lakh``) keep the run contiguous but add no digits.
    """
    v = vocab or load_vocab()
    runs: list[NumRun] = []
    cur_digits: list[str] = []
    cur: list[int] = []  # token indices
    plus_flag = False

    def close() -> None:
        nonlocal cur_digits, cur, plus_flag
        if cur:
            runs.append(
                NumRun("".join(cur_digits), cur[0], cur[-1], toks[cur[0]].t0, toks[cur[-1]].t1, plus_flag)
            )
        cur_digits, cur, plus_flag = [], [], False

    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        piece: str | None = None
        span = 1
        if t.text in v.repeat and i + 1 < n:
            nxt = digit_value(toks[i + 1].text, v)
            if nxt is not None and len(nxt) == 1 and toks[i + 1].t0 - t.t1 < gap_s:
                piece, span = nxt * v.repeat[t.text], 2
        elif t.text in v.ninety and i + 1 < n and digit_value(toks[i + 1].text, v) == "1":
            piece, span = "91", 2
        elif t.text in v.compound:
            piece = ""
        else:
            piece = digit_value(t.text, v)
        if piece is None:
            if t.text in v.plus and not cur:  # "plus nine one": a country code follows
                nxt = toks[i + 1] if i + 1 < n else None
                plus_flag = (
                    nxt is not None
                    and nxt.t0 - t.t1 < gap_s
                    and (digit_value(nxt.text, v) is not None or nxt.text in v.repeat or nxt.text in v.ninety)
                )
                i += 1
                continue
            close()
            i += 1
            continue
        if cur and t.t0 - toks[cur[-1]].t1 >= gap_s:
            close()
        cur_digits.append(piece)
        cur.extend(range(i, i + span))
        i += span
    close()
    return runs
