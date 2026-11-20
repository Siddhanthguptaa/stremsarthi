"""SPECIMEN rules (docs/specs/eval.md section 5.1).

Every identifier produced here is built so that it cannot be a real one:
* Aadhaar: 12 digits, Verhoeff-valid, leading digit 0 or 1 (real numbers never start with 0 or 1).
* PAN: ``[A-Z]{3}X[A-Z][0-9]{4}[A-Z]``; the 4th character X is not a valid holder-type letter.
* UPI: ``zz-specimen-<8 hex>@<handle>``.
* Phone: seeded synthetic mobile numbers. Collision with a real subscriber is possible in principle
  (UNVERIFIED); they only ever appear in locally rendered images and audio.

``find_forbidden`` is the lint used by tests to prove no generator emits a pattern that could be real.
"""

from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass

from . import verhoeff

SPECIMEN_WORD = "SPECIMEN"
MIN_WATERMARK_WIDTH_FRACTION = 0.08  # of the image width, spec 5.1 rule 1

#: Real PAN 4th-character holder types. A generated PAN must never use one of these.
VALID_PAN_HOLDER_TYPES = "PCHFATBLJG"
PAN_FAKE_HOLDER = "X"

UPI_HANDLES = ("oksbi", "okhdfcbank", "okaxis", "okicici", "ybl", "paytm", "ibl", "apl", "specimen")
UPI_FAKE_HANDLE = "specimen"

QR_POSITIVE_TEMPLATE = "upi://pay?pa={upi_id}&pn=SPECIMEN%20TEST&am=1&cu=INR"
QR_NEGATIVE_PAYLOADS = ("https://example.com/", "WIFI:T:WPA;S:specimen;P:specimen;;")


def aadhaar_number(rng: random.Random) -> str:
    """Verhoeff-valid 12-digit SPECIMEN number with leading digit 0 or 1, no separators."""
    first = rng.choice("01")
    body = first + "".join(rng.choice(string.digits) for _ in range(10))
    number = body + str(verhoeff.check_digit(body))
    assert len(number) == 12 and verhoeff.is_valid(number)
    return number


def format_aadhaar(number: str) -> str:
    return f"{number[0:4]} {number[4:8]} {number[8:12]}"


def pan_number(rng: random.Random) -> str:
    """SPECIMEN PAN with the 4th character X, e.g. ``ABCXE1234F``."""
    letters = string.ascii_uppercase
    pan = (
        "".join(rng.choice(letters) for _ in range(3))
        + PAN_FAKE_HOLDER
        + rng.choice(letters)
        + "".join(rng.choice(string.digits) for _ in range(4))
        + rng.choice(letters)
    )
    assert re.fullmatch(r"[A-Z]{3}X[A-Z][0-9]{4}[A-Z]", pan)
    return pan


def upi_id(rng: random.Random, handle: str | None = None) -> str:
    suffix = "".join(rng.choice("0123456789abcdef") for _ in range(8))
    return f"zz-specimen-{suffix}@{handle or rng.choice(UPI_HANDLES)}"


def upi_payload(upi: str) -> str:
    return QR_POSITIVE_TEMPLATE.format(upi_id=upi)


def phone_number(rng: random.Random) -> str:
    """Synthetic Indian mobile number (10 digits, first digit 6-9). Never dialled or sent anywhere."""
    return rng.choice("6789") + "".join(rng.choice(string.digits) for _ in range(9))


def pin_code(rng: random.Random) -> str:
    """Random valid-range 6-digit PIN (first digit 1-8)."""
    return rng.choice("12345678") + "".join(rng.choice(string.digits) for _ in range(5))


@dataclass(frozen=True)
class Forbidden:
    kind: str
    text: str


_AADHAAR_RE = re.compile(r"(?<![0-9A-Za-z])(\d{4})[ -]?(\d{4})[ -]?(\d{4})(?![0-9A-Za-z])")
_PAN_RE = re.compile(r"(?<![0-9A-Za-z])[A-Z]{3}([A-Z])[A-Z][0-9]{4}[A-Z](?![0-9A-Za-z])")


def find_forbidden(text: str) -> list[Forbidden]:
    """Patterns that could be a real Aadhaar or PAN.

    * a 12-digit run that is Verhoeff-valid with a leading digit 2-9
    * a PAN-shaped string whose 4th character is a valid holder-type letter
    """
    found: list[Forbidden] = []
    for m in _AADHAAR_RE.finditer(text):
        digits = "".join(m.groups())
        if digits[0] in "23456789" and verhoeff.is_valid(digits):
            found.append(Forbidden("aadhaar_like", digits))
    for m in _PAN_RE.finditer(text):
        if m.group(1) in VALID_PAN_HOLDER_TYPES:
            found.append(Forbidden("pan_like", m.group(0)))
    return found


def scan_obj(obj: object) -> list[Forbidden]:
    """Recursively scan every string in a JSON-like structure."""
    out: list[Forbidden] = []
    if isinstance(obj, str):
        out.extend(find_forbidden(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(scan_obj(k))
            out.extend(scan_obj(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(scan_obj(v))
    return out
