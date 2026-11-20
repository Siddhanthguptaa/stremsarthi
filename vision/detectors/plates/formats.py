"""Indian registration-plate formats on normalised OCR text (spec 5.8): standard, single-digit RTO, Bharat series.

Pure text, no models. The text lives for one frame only: never log it.

Formats (uppercase alphanumerics, spaces and punctuation already dropped):

* standard  ``^[A-Z]{2}\\d{2}[A-Z]{1,3}\\d{4}$``  (``KA01AB1234``)
* older / single-digit RTO ``^[A-Z]{2}\\d{1,2}[A-Z]{1,3}\\d{4}$``  (``DL1CAB1234``)
* Bharat series ``^\\d{2}BH\\d{4}[A-Z]{1,2}$``  (``22BH1234AA``)

OCR confusions are repaired by position (state letters, RTO digits, series letters, serial digits), at most
``MAX_REPAIRS`` per plate; a plate that needed a repair must also carry a known state or union-territory code,
so noise such as part numbers is not turned into a plate by aggressive repair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STANDARD_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$")
BH_RE = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")

# State and union-territory codes in use on registration plates (incl. the retired OR, the merged DD/DN, TG).
STATE_CODES = frozenset(
    "AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL OD OR PB PY RJ SK TG TN "
    "TR TS UK UP WB".split()
)

MAX_REPAIRS = 2
CONF_EXACT = 0.9
CONF_EXACT_UNKNOWN_STATE = 0.6
CONF_REPAIR_STEP = 0.1
CONF_BH_LATE_YEAR = 0.9  # BH series started in 2021
CONF_BH_OTHER = 0.7
STRIP_PREFIXES = ("IND", "1ND", "ND", "IN")  # the blue "IND" strip is sometimes read with the text

_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
_SERIES_TO_LETTER = {**_TO_LETTER, "0": "D", "1": "L"}  # series letters skip I and O
_TO_DIGIT = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "T": "7",
    "B": "8",
}
_BH_LETTER = {"8": "B", "6": "B", "4": "H", "1": "H"}


@dataclass(frozen=True, slots=True)
class PlateMatch:
    series: str  # "standard" | "bh"
    confidence: float
    repairs: int
    state_known: bool  # standard plates only
    rto_digits: int  # standard: 1 or 2; BH: 2


def alnum(text: str) -> str:
    """Uppercase ASCII letters and digits only (everything else, including spaces and dashes, dropped)."""
    return "".join(c for c in text.upper() if c.isascii() and c.isalnum())


def _fit(s: str, kinds: str, letter_maps: list[dict[str, str]]) -> tuple[str, int] | None:
    """Fit ``s`` to a pattern of kinds (``L`` letter, ``D`` digit); returns (repaired, repairs) or None."""
    out: list[str] = []
    repairs = 0
    for ch, kind, lmap in zip(s, kinds, letter_maps, strict=True):
        if kind == "D":
            if ch.isdigit():
                out.append(ch)
            elif ch in _TO_DIGIT:
                out.append(_TO_DIGIT[ch])
                repairs += 1
            else:
                return None
        else:
            if ch.isalpha():
                out.append(ch)
            elif ch in lmap:
                out.append(lmap[ch])
                repairs += 1
            else:
                return None
    return "".join(out), repairs


def _standard(s: str) -> PlateMatch | None:
    best: PlateMatch | None = None
    n = len(s)
    for rto in (2, 1):
        for series in (1, 2, 3):
            if 2 + rto + series + 4 != n:
                continue
            kinds = "LL" + "D" * rto + "L" * series + "DDDD"
            maps = [_TO_LETTER] * 2 + [_TO_LETTER] * rto + [_SERIES_TO_LETTER] * series + [_TO_LETTER] * 4
            fit = _fit(s, kinds, maps)
            if fit is None:
                continue
            fixed, repairs = fit
            if repairs > MAX_REPAIRS:
                continue
            if int(fixed[2 : 2 + rto]) == 0:
                continue  # there is no RTO 0 / 00
            known = fixed[:2] in STATE_CODES
            if (repairs or n <= 9) and not known:
                continue  # repaired or short readings need a real state code
            conf = (CONF_EXACT if known else CONF_EXACT_UNKNOWN_STATE) - CONF_REPAIR_STEP * repairs
            cand = PlateMatch("standard", conf, repairs, known, rto)
            if best is None or (cand.repairs, -cand.confidence) < (best.repairs, -best.confidence):
                best = cand
    return best


def _bh(s: str) -> PlateMatch | None:
    n = len(s)
    if n not in (9, 10):
        return None
    kinds = "DD" + "LL" + "DDDD" + "L" * (n - 8)
    maps = [_TO_LETTER] * 2 + [_BH_LETTER] * 2 + [_TO_LETTER] * 4 + [_SERIES_TO_LETTER] * (n - 8)
    fit = _fit(s, kinds, maps)
    if fit is None:
        return None
    fixed, repairs = fit
    if fixed[2:4] != "BH" or repairs > MAX_REPAIRS:
        return None
    year = int(fixed[:2])
    conf = (CONF_BH_LATE_YEAR if year >= 21 else CONF_BH_OTHER) - CONF_REPAIR_STEP * repairs
    return PlateMatch("bh", conf, repairs, True, 2)


def match_plate(text: str) -> PlateMatch | None:
    """The plate format ``text`` matches as a whole (spaces and punctuation ignored), or None."""
    s = alnum(text)
    candidates = [s]
    for prefix in STRIP_PREFIXES:
        if s.startswith(prefix) and len(s) > len(prefix) + 7:
            candidates.append(s[len(prefix) :])
    best: PlateMatch | None = None
    for cand in candidates:
        if not 8 <= len(cand) <= 11:
            continue
        for found in (_bh(cand), _standard(cand)):
            if found is not None and (best is None or found.confidence > best.confidence):
                best = found
    return best
