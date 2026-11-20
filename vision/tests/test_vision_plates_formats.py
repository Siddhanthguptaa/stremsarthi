"""Plate format matching on normalised text (CPU, no models). Every string is a SPECIMEN or a fake."""

from __future__ import annotations

import pytest

from vision.detectors.plates.formats import STATE_CODES, alnum, match_plate


@pytest.mark.parametrize(
    ("text", "series", "repairs"),
    [
        ("KA01AB1234", "standard", 0),
        ("KA 01 AB 1234", "standard", 0),
        ("mh-12-de-4567", "standard", 0),
        ("DL1CAB1234", "standard", 0),  # single-digit RTO, three series letters
        ("DL 1 C AB 1234", "standard", 0),
        ("KA05A1234", "standard", 0),  # one series letter
        ("TN22ABC1234", "standard", 0),
        ("22BH1234AA", "bh", 0),
        ("22 BH 1234 A", "bh", 0),
        ("IND KA 01 AB 1234", "standard", 0),  # the blue strip read with the text
        ("IND 22 BH 1234 AA", "bh", 0),
        ("KA0lAB1234", "standard", 1),  # l for 1 in the RTO digits
        ("KA01AB12B4", "standard", 1),  # B for 8 in the serial
        ("K401AB1234", "standard", 1),  # 4 for A in the state code
        ("22BH123SAA", "bh", 1),  # S for 5
        ("2ZBH1234AA", "bh", 1),  # Z for 2
    ],
)
def test_valid_plates_match(text: str, series: str, repairs: int) -> None:
    m = match_plate(text)
    assert m is not None and m.series == series and m.repairs == repairs
    assert 0.5 <= m.confidence <= 0.9


@pytest.mark.parametrize(
    "text",
    [
        "",
        "KA 01 123",  # serial too short
        "KA01AB99999",  # serial too long (no repair makes it fit)
        "1234 5678",
        "OM SAI RAM",
        "BABY ON BOARD",
        "SPECIMEN CAR",
        "ORDER12345",
        "9876543210",
        "KA01AB1234 parked outside",  # a plate inside a sentence is not a plate reading
        "22BH12AA",  # BH needs four digits
        "22XX1234AA",  # not BH
    ],
)
def test_non_plates_do_not_match(text: str) -> None:
    assert match_plate(text) is None


def test_unknown_state_is_accepted_only_exact_and_with_lower_confidence() -> None:
    exact = match_plate("ZZ01AB1234")
    assert exact is not None and not exact.state_known and exact.confidence < 0.7
    known = match_plate("KA01AB1234")
    assert known is not None and known.state_known and known.confidence > exact.confidence
    assert match_plate("ZZ0lAB1234") is None  # a repaired reading needs a real state code
    assert match_plate("ZZ1A1234") is None  # a short reading needs a real state code too


def test_repairs_are_capped_and_lower_the_confidence() -> None:
    clean = match_plate("KA01AB1234")
    one = match_plate("KA0lAB1234")
    assert clean is not None and one is not None and one.confidence < clean.confidence
    assert match_plate("KAOlAB12B4") is None  # three repairs


def test_bh_year_sets_the_confidence() -> None:
    late, early = match_plate("23BH1234AA"), match_plate("19BH1234AA")
    assert late is not None and early is not None and late.confidence > early.confidence


def test_alnum_drops_everything_but_ascii_letters_and_digits() -> None:
    assert alnum("ka-01 ab.1234|") == "KA01AB1234"
    assert alnum("कख 12") == "12"


def test_state_table_has_the_common_codes() -> None:
    for code in ("KA", "MH", "DL", "TN", "TS", "UP", "WB", "GJ", "RJ", "KL", "AP", "TG"):
        assert code in STATE_CODES
