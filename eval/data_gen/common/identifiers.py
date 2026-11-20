"""Synthetic identifiers for the video generators: plates, emails, phone and PIN renderings, OTP, game IDs.

All values are random and only ever drawn into locally rendered SPECIMEN images. A random value can
collide with a real one in principle (UNVERIFIED, accepted by docs/specs/eval.md 5.1 rules 5-7), which is why
every image also carries a SPECIMEN mark. Formats mirror the vision spec regexes (docs/specs/vision.md 5.8-5.9).
"""

from __future__ import annotations

import random
import re
import string

from . import specimen

# ---- plates -----------------------------------------------------------------------------------------------
STATE_CODES = (
    "KA", "MH", "DL", "TN", "TS", "AP", "KL", "GJ", "RJ", "UP", "WB", "MP", "HR", "PB", "BR", "OD", "AS",
    "UK", "HP", "CG", "JH", "GA",
)  # fmt: skip
SERIES_LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"  # Indian series skip I and O (Q is rare, left out)

PLATE_STANDARD_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$")
PLATE_BH_RE = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")


def plate_standard(rng: random.Random) -> str:
    """``KA01AB1234`` style registration (compact, no spaces)."""
    n_letters = rng.choices((1, 2, 3), weights=(1, 6, 2))[0]
    series = "".join(rng.choice(SERIES_LETTERS) for _ in range(n_letters))
    plate = f"{rng.choice(STATE_CODES)}{rng.randint(1, 70):02d}{series}{rng.randint(1, 9999):04d}"
    assert PLATE_STANDARD_RE.fullmatch(plate)
    return plate


def plate_bh(rng: random.Random) -> str:
    """Bharat series ``22BH1234AA``."""
    series = "".join(rng.choice(SERIES_LETTERS) for _ in range(rng.choice((1, 2))))
    plate = f"{rng.randint(21, 30):02d}BH{rng.randint(1, 9999):04d}{series}"
    assert PLATE_BH_RE.fullmatch(plate)
    return plate


def plate_lines(plate: str, two_line: bool) -> list[str]:
    """Display lines for a compact plate: one line ``KA 01 AB 1234`` or two lines ``KA 01`` / ``AB 1234``."""
    if PLATE_BH_RE.fullmatch(plate):
        parts = [plate[:2], "BH", plate[4:8], plate[8:]]
        return [f"{parts[0]} {parts[1]}", f"{parts[2]} {parts[3]}"] if two_line else [" ".join(parts)]
    m = re.fullmatch(r"([A-Z]{2})(\d{2})([A-Z]{1,3})(\d{4})", plate)
    assert m, plate
    a, b, c, d = m.groups()
    return [f"{a} {b}", f"{c} {d}"] if two_line else [f"{a} {b} {c} {d}"]


def plate_invalid(rng: random.Random) -> str:
    """Text that looks plate-like but fails every plate regex (hard negative)."""
    kind = rng.choice(("short", "digits", "long", "words"))
    if kind == "short":
        return f"{rng.choice(STATE_CODES)} {rng.randint(1, 99):02d} {rng.randint(100, 999)}"
    if kind == "digits":
        return f"{rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
    if kind == "long":
        return f"{rng.choice(STATE_CODES)}{rng.randint(1, 70):02d}{'ABCD'[: rng.randint(4, 4)]}{rng.randint(10000, 99999)}"
    return rng.choice(("OM SAI RAM", "BABY ON BOARD", "SPECIMEN CAR", "PRESS", "JAI MATA DI", "NO PARKING"))


# ---- digits and phone numbers --------------------------------------------------------------------------------
DEVANAGARI_DIGITS = "०१२३४५६७८९"
_TO_DEVANAGARI = str.maketrans(string.digits, DEVANAGARI_DIGITS)
_FROM_DEVANAGARI = str.maketrans(DEVANAGARI_DIGITS, string.digits)

PHONE_RE = re.compile(r"(?<!\d)(?:(?:\+?91|0)[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")  # vision spec 5.9
PIN_RE = re.compile(r"(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)")
UPI_ID_RE = re.compile(r"[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9]{1,31}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PHONE_FORMS = (
    "plain",
    "spaced",
    "plus91",
    "plus91_dash",
    "zero",
    "hyphen",
    "devanagari",
    "devanagari_spaced",
)


def to_devanagari_digits(s: str) -> str:
    return s.translate(_TO_DEVANAGARI)


def from_devanagari_digits(s: str) -> str:
    return s.translate(_FROM_DEVANAGARI)


def format_phone(number: str, form: str) -> str:
    """Render a 10-digit mobile number in one of the on-screen forms (see ``PHONE_FORMS``)."""
    assert len(number) == 10 and number.isdigit()
    a, b = number[:5], number[5:]
    return {
        "plain": number,
        "spaced": f"{a} {b}",
        "plus91": f"+91 {a} {b}",
        "plus91_dash": f"+91-{number}",
        "zero": f"0{a} {b}",
        "hyphen": f"{a}-{b}",
        "devanagari": to_devanagari_digits(number),
        "devanagari_spaced": to_devanagari_digits(f"{a} {b}"),
    }[form]


def non_mobile_number(rng: random.Random) -> str:
    """10-digit run starting with 1-5: not an Indian mobile number (hard negative for the phone detector)."""
    return rng.choice("12345") + "".join(rng.choice(string.digits) for _ in range(9))


def non_pin_number(rng: random.Random) -> str:
    """Six digits that are not a PIN code: leading zero, so ``[1-9]\\d{2}`` PIN patterns do not match."""
    return "0" + "".join(rng.choice(string.digits) for _ in range(5))


PIN_CUES = (
    "PIN: {pin}",
    "Pincode: {pin}",
    "PIN Code - {pin}",
    "Pin {pin}",
    "Bengaluru - {pin}",
    "Mumbai {pin}",
    "Delhi - {pin}",
    "Chennai {pin}",
    "पिन कोड: {pin}",
    "पिन {pin}",
)


# ---- email / UPI ---------------------------------------------------------------------------------------------
_FIRST = ("aarav", "diya", "kabir", "meera", "rohan", "sana", "tanvi", "vikram", "isha", "arjun")
_LAST = ("specimen", "testuser", "sample", "demo", "mock")
EMAIL_DOMAINS = ("specimen.invalid", "example.invalid", "mail.specimen.invalid")


def email_address(rng: random.Random) -> str:
    """Fake email on a ``.invalid`` domain (RFC 2606: can never resolve)."""
    addr = f"{rng.choice(_FIRST)}.{rng.choice(_LAST)}{rng.randint(1, 99)}@{rng.choice(EMAIL_DOMAINS)}"
    assert EMAIL_RE.fullmatch(addr)
    return addr


def social_handle(rng: random.Random) -> str:
    """``@streamer_name`` mention: not an email and not a UPI ID (hard negative)."""
    return (
        "@"
        + rng.choice(_FIRST)
        + "_"
        + rng.choice(("gaming", "live", "plays", "ff", "bgmi"))
        + str(rng.randint(1, 99))
    )


# ---- OTP and games -------------------------------------------------------------------------------------------
def otp_code(rng: random.Random, digits: int | None = None) -> str:
    n = digits or rng.choices((4, 6, 8), weights=(2, 7, 1))[0]
    return "".join(rng.choice(string.digits) for _ in range(n))


def bgmi_player_id(rng: random.Random) -> str:
    """10-11 digit style ID (vision spec 5.10: 8-12 digits, UNVERIFIED lengths)."""
    return "5" + "".join(rng.choice(string.digits) for _ in range(rng.choice((9, 10))))


def freefire_uid(rng: random.Random) -> str:
    return rng.choice("123") + "".join(rng.choice(string.digits) for _ in range(rng.choice((8, 9))))


def room_id(rng: random.Random) -> str:
    return "".join(rng.choice(string.digits) for _ in range(rng.choice((6, 7, 8))))


def room_password(rng: random.Random) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(rng.choice(alphabet) for _ in range(rng.choice((4, 5, 6))))


def upi_id(rng: random.Random, handle: str | None = None) -> str:
    return specimen.upi_id(rng, handle)
