"""UPI IDs (``name@bank``) in OCR text, kept apart from e-mail addresses.

A UPI ID is ``[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,31}``: the part after ``@`` is a bank handle with no
dot. An address whose domain has a dot (``name@gmail.com``) is an e-mail (``email.py``). Confidence is 0.9
for well-known handles, 0.6 otherwise (a generic ``word@word`` can be anything).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The handle may start with a digit only because OCR reads ``oksbi`` as ``0ksbi``: such a match is kept only when the
# repaired handle is a well-known one (``find_upi_ids``).
UPI_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])(?P<name>[A-Za-z0-9._-]{2,64})@(?P<handle>[A-Za-z0-9][A-Za-z0-9]{1,31})(?![A-Za-z0-9@-]|\.[A-Za-z0-9])"
)
_HANDLE_CONFUSIONS = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b"})

KNOWN_HANDLES: frozenset[str] = frozenset(
    {
        "oksbi",
        "okhdfcbank",
        "okicici",
        "okaxis",
        "ybl",
        "ibl",
        "axl",
        "paytm",
        "apl",
        "upi",
        "sbi",
        "hdfcbank",
        "icici",
        "axisbank",
        "pnb",
        "ptyes",
        "pthdfc",
        "ptsbi",
        "ptaxis",
        "okbizaxis",
        "freecharge",
        "idfcbank",
        "kotak",
        "yesbank",
        "indus",
        "barodampay",
        "cnrb",
        "unionbank",
        "aubank",
        "jupiteraxis",
        "fbl",
        "airtel",
    }
)

CONF_KNOWN = 0.9
CONF_OTHER = 0.6


@dataclass(frozen=True, slots=True)
class UpiHit:
    start: int
    end: int
    confidence: float
    known: bool


def find_upi_ids(text: str) -> list[UpiHit]:
    """Every UPI-ID-shaped token in ``text`` (an address with a dotted domain is not one)."""
    hits: list[UpiHit] = []
    for m in UPI_RE.finditer(text):
        handle = m.group("handle").casefold()
        known = handle in KNOWN_HANDLES or handle.translate(_HANDLE_CONFUSIONS) in KNOWN_HANDLES
        if handle[0].isdigit() and not known:
            continue  # a digit-led handle that does not repair to a bank handle is not a UPI ID
        hits.append(UpiHit(m.start(), m.end(), CONF_KNOWN if known else CONF_OTHER, known))
    return hits


__all__ = ["CONF_KNOWN", "CONF_OTHER", "KNOWN_HANDLES", "UPI_RE", "UpiHit", "find_upi_ids"]
