"""Verhoeff, Aadhaar, PAN and OCR-normalisation helpers (pure text, CPU)."""

from __future__ import annotations

import random

import pytest

from vision.ocr.normalize import (
    digit_ratio,
    normalise_text,
    repair_digit_field,
    script_of,
    to_ascii_digits,
)
from vision.patterns import (
    digit_run_hits,
    find_aadhaar,
    find_masked_aadhaar_hint,
    find_pan,
    is_aadhaar_number,
    repair_pan,
)
from vision.patterns import verhoeff as v


def _independent_check_digit(payload: str) -> int:
    """Brute force from the definition (not the table walk used by the module): try each digit."""
    for c in range(10):
        if v.checksum(payload + str(c)) == 0:
            return c
    raise AssertionError("no check digit")


def _specimen_numbers(n: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        body = rng.choice("01") + "".join(rng.choice("0123456789") for _ in range(10))
        out.append(body + str(v.check_digit(body)))
    return out


def test_verhoeff_known_vectors() -> None:
    # Wikipedia / reference vectors: 236 -> 3, 12345 -> 1, 142857 -> 0, 75872 -> 2
    assert v.check_digit("236") == 3 and v.is_valid("2363")
    assert v.check_digit("12345") == 1 and v.is_valid("123451")
    assert v.check_digit("142857") == 0 and v.is_valid("1428570")
    assert v.check_digit("75872") == 2 and v.is_valid("758722")
    assert not v.is_valid("2364")
    assert v.checksum("2363") == 0
    assert v.generate("236") == "2363"


def test_verhoeff_rejects_junk_without_raising() -> None:
    for junk in ("", "abc", "12a4", "१२३४", "12-34"):
        assert v.is_valid(junk) is False
    with pytest.raises(ValueError):
        v.check_digit("12x")


def test_generated_specimen_numbers_validate_and_agree_with_brute_force() -> None:
    numbers = _specimen_numbers(100)
    assert len(set(numbers)) > 90
    for num in numbers:
        assert len(num) == 12 and num[0] in "01"
        assert v.is_valid(num) and is_aadhaar_number(num)
        assert _independent_check_digit(num[:-1]) == int(num[-1])


def test_every_single_digit_change_and_adjacent_transposition_fails() -> None:
    for num in _specimen_numbers(30, seed=11):
        for i in range(12):
            for d in "0123456789":
                if d != num[i]:
                    assert not v.is_valid(num[:i] + d + num[i + 1 :]), (i, d)
        for i in range(11):
            if num[i] != num[i + 1]:
                swapped = num[:i] + num[i + 1] + num[i] + num[i + 2 :]
                assert not v.is_valid(swapped), i


def test_eval_generator_agrees_with_our_verhoeff() -> None:
    from eval.data_gen.common import verhoeff as ev

    for num in _specimen_numbers(50, seed=3):
        assert ev.is_valid(num)
        assert ev.check_digit(num[:-1]) == v.check_digit(num[:-1])


@pytest.mark.parametrize(
    "fmt", ["{a} {b} {c}", "{a}{b}{c}", "{a}-{b}-{c}", "UID: {a} {b} {c}", "{a} {b} {c} x"]
)
def test_aadhaar_runs_found_in_text(fmt: str) -> None:
    num = _specimen_numbers(1, seed=5)[0]
    text = fmt.format(a=num[:4], b=num[4:8], c=num[8:])
    hits = find_aadhaar(text)
    assert len(hits) == 1 and hits[0].valid


def test_aadhaar_invalid_or_longer_numbers_do_not_count() -> None:
    num = _specimen_numbers(1, seed=5)[0]
    bad = num[:-1] + str((int(num[-1]) + 1) % 10)
    assert find_aadhaar(bad) == []
    assert [h.valid for h in digit_run_hits(bad)] == [False]
    assert find_aadhaar("1234 5678 9012 3456") == []  # a 16-digit grouped number is not an Aadhaar
    assert find_aadhaar(num + "7") == []  # 13 digits
    assert find_aadhaar("0123 456") == []


def test_masked_aadhaar_hint() -> None:
    assert find_masked_aadhaar_hint("XXXX XXXX 1234")
    assert find_masked_aadhaar_hint("xxxx-xxxx-1234")
    assert not find_masked_aadhaar_hint("1234 5678 1234")


@pytest.mark.parametrize(
    "text",
    ["ABCDE1234F", "x ABCPE1234F y", "ABCXE1234F", "PAN: AAAPL1234C."],
)
def test_pan_positive(text: str) -> None:
    hits = find_pan(text)
    assert hits and not hits[0].repaired


@pytest.mark.parametrize("text", ["ABCD1234FG", "ABCDE12345", "abcde1234f1", "ABCDEF1234F", "1BCDE1234F", ""])
def test_pan_negative(text: str) -> None:
    assert not any(not h.repaired for h in find_pan(text))


def test_pan_holder_type_sets_confidence() -> None:
    assert find_pan("ABCPE1234F")[0].confidence == pytest.approx(0.9)  # P = individual
    assert find_pan("ABCXE1234F")[0].confidence == pytest.approx(0.6)  # X: SPECIMEN holder type


def test_pan_repair_by_position() -> None:
    assert repair_pan("A8CPE1Z34F") == "ABCPE1234F"  # 8 -> B (letter slot) and Z -> 2 (digit slot)
    assert repair_pan("ABCPE1234F") == "ABCPE1234F"
    assert repair_pan("ABCPEI234F") == "ABCPE1234F"  # I -> 1 in a digit slot
    assert repair_pan("A8CPE1234F") == "ABCPE1234F"  # 8 -> B in a letter slot
    hits = find_pan("ABCPEI234F")
    assert hits and hits[0].repaired and hits[0].confidence == pytest.approx(0.8)
    assert repair_pan("short") is None


def test_normalisation_of_indic_digits_and_confusions() -> None:
    assert to_ascii_digits("१२३४ ५६७८") == "1234 5678"  # Devanagari
    assert to_ascii_digits("১২৩") == "123" and to_ascii_digits("௧௨௩") == "123"
    assert to_ascii_digits("౧౨౩") == "123" and to_ascii_digits("೧೨೩") == "123"
    assert normalise_text("１２３４") == "1234"  # full-width
    assert repair_digit_field("O12l 5S8B") == "0121 5588"
    assert digit_ratio("1234 5678") == 1.0 and digit_ratio("SPECIMEN") < 0.5
    assert digit_ratio("") == 0.0


def test_script_labels() -> None:
    assert script_of("AADHAAR 1234") == "latin"
    assert script_of("आधार") == "devanagari"
    assert script_of("ஆதார்") == "tamil"
    assert script_of("ఆధార్") == "telugu"
    assert script_of("ಆಧಾರ್") == "kannada"
    assert script_of("আধার") == "bengali"
    assert script_of("") == "unknown" and script_of("---") == "unknown"
