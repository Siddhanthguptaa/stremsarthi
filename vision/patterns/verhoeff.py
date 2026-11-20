"""Verhoeff checksum (the check Aadhaar numbers carry). Tables from ``docs/specs/vision.md`` 5.6.

``d`` is the dihedral-group multiplication table, ``p`` the position permutation table and ``inv`` the
inverse table. Pure Python; no dependencies.
"""

from __future__ import annotations

D: tuple[tuple[int, ...], ...] = (
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
P: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
INV: tuple[int, ...] = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _digits(number: str) -> list[int]:
    cleaned = number.replace(" ", "")
    if not cleaned or not (cleaned.isascii() and cleaned.isdigit()):
        raise ValueError("Verhoeff input must be ASCII digits (spaces allowed)")
    return [int(c) for c in cleaned]


def checksum(number: str) -> int:
    """Verhoeff checksum of a digit string, check digit included. ``0`` means valid."""
    c = 0
    for i, digit in enumerate(reversed(_digits(number))):
        c = D[c][P[i % 8][digit]]
    return c


def is_valid(number: str) -> bool:
    """True when ``number`` (check digit included) passes the Verhoeff check. Never raises on junk."""
    try:
        return checksum(number) == 0
    except ValueError:
        return False


def check_digit(payload: str) -> int:
    """The digit to append to ``payload`` so that the result passes ``is_valid``."""
    c = 0
    for i, digit in enumerate(reversed(_digits(payload))):
        c = D[c][P[(i + 1) % 8][digit]]
    return INV[c]


def generate(payload: str) -> str:
    """``payload`` followed by its check digit (a Verhoeff-valid number)."""
    return payload.replace(" ", "") + str(check_digit(payload))
