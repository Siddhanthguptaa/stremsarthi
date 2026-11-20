"""Doxxing detection (docs/specs/chat.md 5.1 step 5).

Finds (1) the streamer's configured private terms and phone numbers, (2) Indian mobile numbers in
many spellings (spaced, dashed, ``+91``, Indic digits, spoken digits), (3) PIN codes next to an
address cue, (4) Verhoeff-valid 12-digit numbers (Aadhaar-like) and PAN numbers, (5) intent phrases.

Private terms and phones live in RAM only, are never logged and never appear in a ``repr``.
Findings carry codes only.
"""

from __future__ import annotations

import re
import unicodedata

from contracts import Category

from .candidate import Candidate
from .normalize import Normalized, lexicon_view

_INDIC_DIGITS = {
    ord(chr(base + i)): ord(str(i))
    for base in (0x966, 0x9E6, 0xA66, 0xAE6, 0xB66, 0xBE6, 0xC66, 0xCE6, 0xD66)
    for i in range(10)
}

_SPOKEN: dict[str, str] = {}
for _digit, _words in enumerate(
    [
        ("zero", "shunya", "sifar", "शून्य", "जीरो", "ज़ीरो"),
        ("one", "ek", "एक"),
        ("two", "do", "दो"),
        ("three", "teen", "तीन"),
        ("four", "char", "chaar", "चार"),
        ("five", "panch", "paanch", "पांच", "पाँच"),
        ("six", "chhe", "chhah", "chha", "cheh", "छह", "छः", "छे", "छ"),
        ("seven", "saat", "sat", "सात"),
        ("eight", "aath", "ath", "आठ"),
        ("nine", "nau", "nao", "नौ"),
    ]
):
    for _w in _words:
        _SPOKEN[_w] = str(_digit)
_MULTIPLIER = {"double": 2, "dabal": 2, "dubal": 2, "डबल": 2, "triple": 3, "ट्रिपल": 3}

_TOKEN = re.compile(r"\d+|[a-zऀ-ॿ]+|[^\s]")
_RUN_SEPS = frozenset("-.,()+")
_MOBILE = re.compile(r"(?:0{0,2}91|0)?[6-9]\d{9}")
_COUNTRY = frozenset({"91", "091", "0091", "0"})
_PIN = re.compile(r"(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)")
_ADDRESS_CUE = re.compile(
    r"house|flat|plot|h\.?\s?no|near|road|nagar|colony|gali|mohalla|sector|pin\s?(?:code)?|ghar|address|rehta|rehti|rahta|rahti|pata\b|"
    r"मकान|घर|पता|गली|मोहल्ला|कॉलोनी|नगर|रोड|पिन",
    re.IGNORECASE,
)
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z](?![A-Za-z0-9])", re.IGNORECASE)
_INTENT = re.compile(
    r"(?<![a-z])dox{1,2}(?:ed|ing|er)?(?![a-z])|"
    r"leak\s+(?:his|her|their|uska|iska|uski|iski)\s+(?:home\s+)?(?:address|number|phone|location|pata)|"
    r"ghar\s+ka\s+(?:address|pata)|home\s+address|address\s+(?:leak|share|bhej|batao)|"
    r"(?:uska|iska|uski|iski)\s+(?:address|pata|number)\s+(?:do|batao|bhej|leak)|"
    r"घर\s+का\s+(?:पता|एड्रेस)|एड्रेस\s+लीक"
)

# --- Verhoeff ---------------------------------------------------------------------------------
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)  # fmt: skip
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)  # fmt: skip


def verhoeff_valid(number: str) -> bool:
    if not number.isdigit():
        return False
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def _digit_runs(text: str) -> list[list[str]]:
    """Runs of digit groups: numeric tokens and spoken digits separated by at most ``-.,()+``."""
    runs: list[list[str]] = []
    run: list[str] = []
    mult = 1
    pending_sep = False

    def flush() -> None:
        nonlocal run, mult, pending_sep
        if run:
            runs.append(run)
        run, mult, pending_sep = [], 1, False

    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok.isdigit():
            run.append(tok)
            mult, pending_sep = 1, False
        elif tok in _SPOKEN:
            run.extend([_SPOKEN[tok]] * mult)
            mult, pending_sep = 1, False
        elif tok in _MULTIPLIER:
            mult = _MULTIPLIER[tok]
            pending_sep = False
        elif tok in _RUN_SEPS and run and not pending_sep:
            pending_sep = True
        else:
            flush()
    flush()
    return runs


def _plausible(groups: list[str]) -> bool:
    """Reject accidental concatenations (counting, scores): groups must look like a written number."""
    if len(groups) == 1:
        return True
    body = groups[1:] if groups[0] in _COUNTRY else groups
    if all(len(g) == 1 for g in groups) or all(len(g) == 2 for g in body):
        return True
    return all(len(g) >= 3 for g in body)


def find_numbers(text: str) -> list[tuple[str, str]]:
    """``[(kind, digits)]`` for mobile numbers (``phone``) and Verhoeff-valid 12-digit numbers (``aadhaar``)."""
    found: list[tuple[str, str]] = []
    for run in _digit_runs(text.translate(_INDIC_DIGITS).lower()):
        run = run[:40]
        for i in range(len(run)):
            acc = ""
            for j in range(i, len(run)):
                acc += run[j]
                if len(acc) > 14:
                    break
                groups = run[i : j + 1]
                if len(acc) < 10 or not _plausible(groups):
                    continue
                if _MOBILE.fullmatch(acc):
                    found.append(("phone", acc))
                elif len(acc) == 12 and acc[0] in "23456789" and verhoeff_valid(acc):
                    found.append(("aadhaar", acc))
    return found


class DoxxingDetector:
    """Holds the streamer's private terms and phones in RAM and scans messages."""

    def __init__(self, terms: list[str] | None = None, phones: list[str] | None = None) -> None:
        self._term_re: re.Pattern[str] | None = None
        self._phones: frozenset[str] = frozenset()
        self.update(terms or [], phones or [])

    def __repr__(self) -> str:  # never print private data
        return "DoxxingDetector(<private>)"

    def update(self, terms: list[str], phones: list[str]) -> None:
        views = sorted({lexicon_view(t) for t in terms if len(lexicon_view(t)) >= 3}, key=len, reverse=True)
        parts = []
        for v in views:
            esc = re.escape(v)
            lead = r"(?<![a-z0-9])" if v[0].isascii() and v[0].isalnum() else ""
            trail = r"(?![a-z0-9])" if v[-1].isascii() and v[-1].isalnum() else ""
            parts.append(f"{lead}{esc}{trail}")
        self._term_re = re.compile("|".join(parts)) if parts else None
        digits = {
            re.sub(r"\D", "", unicodedata.normalize("NFKC", p).translate(_INDIC_DIGITS)) for p in phones
        }
        self._phones = frozenset(d[-10:] for d in digits if len(d) >= 10)

    def detect(self, norm: Normalized, *, include_private: bool = True) -> list[Candidate]:
        out: list[Candidate] = []
        seen_reasons: set[str] = set()
        if include_private and self._term_re is not None and self._term_re.search(norm.text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.97, "dox_private_term"))
        link = norm.link_text
        numbers = find_numbers(link) if len(link) >= 10 else []
        for kind, digits in numbers:
            if kind == "phone":
                private = include_private and digits[-10:] in self._phones
                reason, score = ("dox_private_phone" if private else "dox_phone"), 0.97
            else:
                reason, score = "dox_aadhaar_like", 0.9
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                out.append(Candidate(Category.CHAT_DOXXING, score, reason))
        for m in _PIN.finditer(link):
            lo, hi = max(0, m.start() - 30), min(len(link), m.end() + 30)
            if _ADDRESS_CUE.search(link[lo : m.start()] + " " + link[m.end() : hi]):
                out.append(Candidate(Category.CHAT_DOXXING, 0.9, "dox_pin_address"))
                break
        if _PAN.search(link):
            out.append(Candidate(Category.CHAT_DOXXING, 0.9, "dox_pan"))
        if _INTENT.search(norm.text):
            out.append(Candidate(Category.CHAT_DOXXING, 0.9, "dox_intent"))
        return out
