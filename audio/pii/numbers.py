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
    at_partial: tuple[tuple[str, ...], ...]
    at_fuzzy: tuple[tuple[str, ...], ...]
    handles: frozenset[str]
    cue_generic: tuple[tuple[str, ...], ...]
    cue_address_open: tuple[tuple[str, ...], ...]
    addr_strong: tuple[tuple[str, ...], ...]
    addr_weak: tuple[tuple[str, ...], ...]
    name_stop: frozenset[str]
    cue_filler: frozenset[str]


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
        at_partial=phrases(raw["at_markers"].get("partial", [])),
        at_fuzzy=tuple(tuple(p) for p in raw["at_markers"].get("fuzzy", [])),  # "*" matches any one token
        handles=words(raw["handles"]),
        cue_generic=phrases(raw["cues"]["generic"]),
        cue_address_open=phrases(raw["cues"]["address_open"]),
        addr_strong=phrases(raw["address"]["strong"]),
        addr_weak=phrases(raw["address"]["weak"]),
        name_stop=words(raw["name_stop"]),
        cue_filler=words(raw.get("cue_filler", [])),
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
    spelled: bool = False  # several spoken letters joined into one token (``o k s b i``)


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
                    True,
                )
            )
            i = j + 1
        else:
            out.append(toks[i])
            i += 1
    return out


FOR_WORDS = frozenset({"for", "फॉर"})


def collapse_for_words(toks: Sequence[Tok], max_gap_s: float = 0.6) -> list[Tok]:
    """``z for zebra`` is the letter ``z``: the clarifying word is dropped into the letter's time span."""
    out: list[Tok] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if (
            _single_letter(t.text)
            and i + 2 < n
            and toks[i + 1].text in FOR_WORDS
            and toks[i + 2].text.isalpha()
            and toks[i + 2].text.isascii()
            and toks[i + 2].text[0] == t.text
            and toks[i + 1].t0 - t.t1 < max_gap_s
            and toks[i + 2].t0 - toks[i + 1].t1 < max_gap_s
        ):
            out.append(Tok(t.text, t.t0, toks[i + 2].t1, t.approx or toks[i + 2].approx, t.seg_id, t.widx0, toks[i + 2].widx1))
            i += 3
        else:
            out.append(t)
            i += 1
    return out


def prepare(words: Sequence[Word]) -> list[Tok]:
    return join_single_letters(collapse_for_words(tokenize(words)))


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
    digits: str  # expanded digits ("" if only compound words such as "sau"); "?" stands for a bridged token
    tok0: int  # index range into the token list
    tok1: int
    t0: float
    t1: float
    plus: bool  # preceded by "plus" (a country code follows)
    singles: int = 0  # digits that were spoken one by one (``nau``, ``5``), not as a multi-digit numeral
    compound: bool = False  # contains ``sau`` / ``hazaar`` ...: a quantity, not a digit string
    counting: bool = False  # 1 2 3 / 5 4 3 ...: someone counting, not a code

    @property
    def n(self) -> int:
        return len(self.digits)


# English two-digit number words ("ninety eight" is 98): read as digit pairs inside a run.
EN_TEENS = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
}  # fmt: skip
EN_TENS = {
    "twenty": "2", "thirty": "3", "forty": "4", "fourty": "4", "fifty": "5", "sixty": "6", "seventy": "7",
    "eighty": "8", "ninety": "9",
}  # fmt: skip
BRIDGE_MAX_S = 0.8  # a bridged token is at most this long
# Words that are real connectors, not a mis-heard digit: they end a number run.
BRIDGE_STOP = frozenset(
    {"aur", "and", "or", "ya", "phir", "then", "toh", "to", "plus", "at", "और", "या", "तो"}
)


def _is_counting(digits: str) -> bool:
    if len(digits) < 3 or not digits.isdigit():
        return False
    steps = {int(b) - int(a) for a, b in zip(digits, digits[1:], strict=False)}
    return steps == {1} or steps == {-1}


def _digit_token(t: Tok, v: Vocab) -> bool:
    return digit_value(t.text, v) is not None or t.text in v.repeat or t.text in v.ninety


def number_runs(toks: Sequence[Tok], gap_s: float = 1.0, vocab: Vocab | None = None) -> list[NumRun]:
    """Maximal runs of number tokens whose gaps are below ``gap_s`` (a run continues across segments).

    ``double``/``triple`` repeat the next single digit; ``ninety one`` is read as 91; compound words
    (``sau``, ``hazaar``, ``lakh``) keep the run contiguous but add no digits. One short unknown token between
    two digit tokens is bridged (``?``): a recogniser that mis-hears a digit must not split a number in two.
    """
    v = vocab or load_vocab()
    runs: list[NumRun] = []
    cur_digits: list[str] = []
    cur: list[int] = []  # token indices
    plus_flag = False
    singles = 0
    compound = False

    def close() -> None:
        nonlocal cur_digits, cur, plus_flag, singles, compound
        if cur:
            digits = "".join(cur_digits)
            runs.append(
                NumRun(
                    digits,
                    cur[0],
                    cur[-1],
                    toks[cur[0]].t0,
                    toks[cur[-1]].t1,
                    plus_flag,
                    singles,
                    compound,
                    not compound and _is_counting(digits),
                )
            )
        cur_digits, cur, plus_flag, singles, compound = [], [], False, 0, False

    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        piece: str | None = None
        span = 1
        one = 0  # single digits this piece contributes
        if t.text in EN_TEENS:
            piece, one = EN_TEENS[t.text], 2
        elif t.text in EN_TENS:
            piece, one = EN_TENS[t.text] + "0", 2
            nxt = digit_value(toks[i + 1].text, v) if i + 1 < n else None
            if nxt is not None and len(nxt) == 1 and nxt != "0" and toks[i + 1].t0 - t.t1 < gap_s:
                piece, span = EN_TENS[t.text] + nxt, 2
        elif t.text in v.repeat and i + 1 < n:
            nxt = digit_value(toks[i + 1].text, v)
            if nxt is not None and len(nxt) == 1 and toks[i + 1].t0 - t.t1 < gap_s:
                piece, span, one = nxt * v.repeat[t.text], 2, v.repeat[t.text]
        elif t.text in v.ninety and i + 1 < n and digit_value(toks[i + 1].text, v) == "1":
            piece, span, one = "91", 2, 2
        elif t.text in v.compound:
            piece = ""
        else:
            piece = digit_value(t.text, v)
            one = 1 if piece is not None and len(piece) == 1 else 0
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
            if (
                cur
                and i + 1 < n
                and t.t0 - toks[cur[-1]].t1 < gap_s * 0.6
                and t.t1 - t.t0 <= BRIDGE_MAX_S
                and t.text not in BRIDGE_STOP
                and _digit_token(toks[i + 1], v)
                and toks[i + 1].t0 - t.t1 < gap_s * 0.6
            ):  # mis-heard digit between two digits: keep the run whole
                cur_digits.append("?")
                cur.append(i)
                i += 1
                continue
            close()
            i += 1
            continue
        if cur and t.t0 - toks[cur[-1]].t1 >= gap_s:
            close()
        cur_digits.append(piece)
        cur.extend(range(i, i + span))
        singles += one
        compound = compound or (t.text in v.compound)
        i += span
    close()
    return runs


# --------------------------------------------------------------------------- spelled ids
def is_letter_token(t: Tok) -> bool:
    """A single spoken letter (``d``) or several joined ones (``rahul`` spelt out: ``r a h u l``)."""
    return t.spelled or _single_letter(t.text)


@dataclass(frozen=True, slots=True)
class IdRun:
    tok0: int
    tok1: int
    t0: float
    t1: float
    letters: int  # spoken letters in the run
    digits: int  # digit tokens in the run


def id_runs(toks: Sequence[Tok], gap_s: float = 0.6, vocab: Vocab | None = None) -> list[IdRun]:
    """Runs of spelled letters and digit tokens, as when someone dictates a UPI id, an e-mail or a PAN.

    ``letters`` leaves out a lone ``a`` or ``i`` (also ordinary words) so that "i do" is not a spelt id; they
    still keep a run together. One short unknown token between two members is bridged, as in ``number_runs``."""
    v = vocab or load_vocab()
    runs: list[IdRun] = []
    i, n = 0, len(toks)

    def like(t: Tok) -> bool:
        return is_letter_token(t) or _digit_token(t, v)

    while i < n:
        if not like(toks[i]):
            i += 1
            continue
        j = i
        while j + 1 < n:
            nxt = toks[j + 1]
            if like(nxt) and nxt.t0 - toks[j].t1 < gap_s:
                j += 1
            elif (
                j + 2 < n
                and like(toks[j + 2])
                and nxt.t0 - toks[j].t1 < gap_s
                and toks[j + 2].t0 - nxt.t1 < gap_s
                and nxt.t1 - nxt.t0 <= BRIDGE_MAX_S
                and nxt.text not in BRIDGE_STOP
                and nxt.text.isalpha()
            ):
                j += 2  # a mis-heard letter or digit between two members
            else:
                break
        members = [t for t in toks[i : j + 1] if like(t)]
        letters = sum(
            len(t.text) if t.spelled else (0 if t.text in ("a", "i") else 1)
            for t in members
            if is_letter_token(t)
        )
        digits = sum(1 for t in members if not is_letter_token(t))
        runs.append(IdRun(i, j, toks[i].t0, toks[j].t1, letters, digits))
        i = j + 1
    return runs
