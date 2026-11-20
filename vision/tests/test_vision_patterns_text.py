"""Pattern tables for phone, PIN, UPI ID and e-mail (pure text, CPU, no models)."""

from __future__ import annotations

import pytest

from vision.ocr.normalize import normalise_text
from vision.patterns import (
    find_emails,
    find_phones,
    find_pins,
    find_upi_ids,
    has_pin_cue,
    is_postal_circle,
)
from vision.patterns.verhoeff import check_digit

PHONES = [
    "9876543210",
    "98765 43210",
    "98765-43210",
    "987-654-3210",
    "+91 98765 43210",
    "+91-9876543210",
    "+919876543210",
    "91 98765 43210",
    "09876543210",
    "0 98765 43210",
    "Call: 6123456789 now",
    "WhatsApp +91 70123 45678.",
    "Ph 8234567890, 9123456780",
]
NOT_PHONES = [
    "1234567890",  # starts with 1
    "5876543210",
    "987654321",  # 9 digits
    "98765432109",  # 11 digits
    "9876543210123",  # 13 digits
    "1800 123 456",
    "12/03/2026",
    "192.168.1.1",
    "Rs 9,876,543,210",
    "",
]


@pytest.mark.parametrize("text", PHONES)
def test_phone_positives(text: str) -> None:
    assert find_phones(text), text


@pytest.mark.parametrize("text", NOT_PHONES)
def test_phone_negatives(text: str) -> None:
    assert not find_phones(text), text


def test_phone_prefix_raises_confidence_and_two_numbers_are_found() -> None:
    plain, prefixed = find_phones("98765 43210")[0], find_phones("+91 98765 43210")[0]
    assert prefixed.confidence > plain.confidence and prefixed.prefixed and not plain.prefixed
    assert len(find_phones("Ph 8234567890, 9123456780")) == 2


def test_an_aadhaar_number_starting_91_is_not_91_plus_a_phone() -> None:
    body = "91987654321"
    number = body + str(check_digit(body))  # a Verhoeff-valid 12-digit run starting 91
    assert len(number) == 12
    grouped = f"{number[:4]} {number[4:8]} {number[8:]}"
    assert not find_phones(number) and not find_phones(grouped)
    assert find_phones("+" + number)  # an explicit +91 prefix is still a phone number


PINS = [
    "560001",
    "560 001",
    "PIN 400001",
    "110 001",
    "Bengaluru - 560001",
    "pincode:600001",
    "999999",
    "Pincode -560001",
]
NOT_PINS = [
    "060001",  # leading zero
    "560",  # too short
    "5600012",  # too long
    "1,23,456",
    "12.345678",
    "100000",  # first two digits not a postal circle
    "2026-12",
    "Order #AB123456",  # alphanumeric id
    "Call centre 1800 123 456",  # a digit group before it
    "560001 2024 987",  # digit groups after it
]


@pytest.mark.parametrize("text", PINS)
def test_pin_positives(text: str) -> None:
    assert find_pins(text), text


@pytest.mark.parametrize("text", NOT_PINS)
def test_pin_negatives(text: str) -> None:
    assert not find_pins(text), text


def test_postal_circles() -> None:
    assert is_postal_circle("560001") and is_postal_circle("110001") and is_postal_circle("999999")
    assert not is_postal_circle("100000") and not is_postal_circle("860001") and not is_postal_circle("0")


@pytest.mark.parametrize(
    ("text", "cue"),
    [
        ("PIN: 560001", True),
        ("Pincode 560001", True),
        ("pin code", True),
        ("पिन कोड: ५६०००१", True),
        ("12, MG Road, Bengaluru", True),
        ("Near City Mall", True),
        ("பின்கோடு", True),
        ("Score 560001", False),
        ("pinned message", False),  # whole words only
        ("spinning wheel", False),
        ("order total", False),
        ("", False),
    ],
)
def test_pin_cues(text: str, cue: bool) -> None:
    assert has_pin_cue(text) is cue


UPIS = [
    ("rahul@oksbi", True),
    ("priya.s@okhdfcbank", True),
    ("9876543210@ybl", True),
    ("amit_k@paytm", True),
    ("name-1@axl", True),
    ("Pay: dev.patel@okaxis.", True),  # trailing full stop
    ("UPI ID: 7097774835@0ksbi", True),  # OCR read the letter o as a zero
    ("kiran07@fancybank", False),  # unknown handle: lower confidence
]
NOT_UPIS = [
    "priya@gmail.com",
    "name@outlook.co.in",
    "@streamer",
    "a@bank",  # name shorter than two characters
    "rahul@",
    "user@123",
    "mail me at x.y@z.org",
]


@pytest.mark.parametrize(("text", "known"), UPIS)
def test_upi_positives(text: str, known: bool) -> None:
    hits = find_upi_ids(text)
    assert len(hits) == 1, text
    assert hits[0].known is known and hits[0].confidence == (0.9 if known else 0.6)


@pytest.mark.parametrize("text", NOT_UPIS)
def test_upi_negatives(text: str) -> None:
    assert not find_upi_ids(text), text


EMAILS = [
    "priya.sharma@gmail.com",
    "Mail: amit_k@outlook.com",
    "a.b@example.co.in",
    "x+tag@mail.example.org.",
]
NOT_EMAILS = ["rahul@oksbi", "@home.com", "name@", "name@com", "no at sign here", "a@b.c"]


@pytest.mark.parametrize("text", EMAILS)
def test_email_positives(text: str) -> None:
    assert len(find_emails(text)) == 1, text


@pytest.mark.parametrize("text", NOT_EMAILS)
def test_email_negatives(text: str) -> None:
    assert not find_emails(text), text


def test_upi_and_email_never_overlap() -> None:
    for text in [*EMAILS, *(t for t, _ in UPIS)]:
        assert not (find_upi_ids(text) and find_emails(text)), text


@pytest.mark.parametrize(
    ("native", "ascii_"),
    [
        ("फोन ९८७६५ ४३२१०", "फोन 98765 43210"),
        ("௯௮௭௬௫ ௪௩௨௧௦", "98765 43210"),
        ("౫౬౦౦౦౧", "560001"),
        ("৫৬০০০১", "560001"),
        ("೫೬೦೦೦೧", "560001"),
    ],
)
def test_indic_digits_reach_the_patterns_as_ascii(native: str, ascii_: str) -> None:
    assert normalise_text(native) == ascii_
    if "98765" in ascii_:
        assert find_phones(normalise_text(native))
    else:
        assert find_pins(normalise_text(native))
