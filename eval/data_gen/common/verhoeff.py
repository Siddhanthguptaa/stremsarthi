"""Verhoeff checksum (the check used by Aadhaar numbers).

Standard dihedral-group tables. Pure Python, no dependencies.
"""

from __future__ import annotations

_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _digits(number: str) -> list[int]:
    cleaned = number.replace(" ", "")
    if not cleaned.isdigit():
        raise ValueError("Verhoeff input must contain digits only")
    return [int(c) for c in cleaned]


def checksum(number: str) -> int:
    """Return the Verhoeff checksum of a digit string (0 means valid)."""
    c = 0
    for i, digit in enumerate(reversed(_digits(number))):
        c = _D[c][_P[i % 8][digit]]
    return c


def is_valid(number: str) -> bool:
    """True when the digit string (check digit included) passes the Verhoeff check."""
    return checksum(number) == 0


def check_digit(payload: str) -> int:
    """Check digit to append to `payload` so the result is Verhoeff-valid."""
    c = 0
    for i, digit in enumerate(reversed(_digits(payload))):
        c = _D[c][_P[(i + 1) % 8][digit]]
    return _INV[c]
