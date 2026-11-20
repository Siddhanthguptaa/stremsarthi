"""SPECIMEN number rules, Verhoeff, and the forbidden-pattern lint (spec 9 tests 2 and 3)."""

import re

from eval.data_gen.common import specimen, verhoeff
from eval.data_gen.common.seeds import derive_seed, py_rng


def test_verhoeff_known_vectors():
    assert verhoeff.check_digit("236") == 3
    assert verhoeff.is_valid("2363")
    assert not verhoeff.is_valid("2364")
    assert verhoeff.is_valid("123456789012") == (verhoeff.checksum("123456789012") == 0)


def test_verhoeff_detects_every_single_digit_error_and_adjacent_swaps():
    rng = py_rng(1, "verhoeff")
    for _ in range(50):
        n = specimen.aadhaar_number(rng)
        assert verhoeff.is_valid(n)
        for i in range(len(n)):
            for d in "0123456789":
                if d != n[i]:
                    assert not verhoeff.is_valid(n[:i] + d + n[i + 1 :])
        for i in range(len(n) - 1):
            if n[i] != n[i + 1]:
                swapped = n[:i] + n[i + 1] + n[i] + n[i + 2 :]
                assert not verhoeff.is_valid(swapped)


def test_aadhaar_generator_valid_and_leading_zero_or_one():
    rng = py_rng(1234, "aadhaar")
    nums = [specimen.aadhaar_number(rng) for _ in range(1000)]
    assert all(len(n) == 12 and n.isdigit() for n in nums)
    assert all(verhoeff.is_valid(n) for n in nums)
    assert all(n[0] in "01" for n in nums)
    assert {n[0] for n in nums} == {"0", "1"}
    assert specimen.format_aadhaar(nums[0]) == f"{nums[0][:4]} {nums[0][4:8]} {nums[0][8:]}"


def test_pan_fourth_char_is_x():
    rng = py_rng(1234, "pan")
    pans = [specimen.pan_number(rng) for _ in range(1000)]
    assert all(re.fullmatch(r"[A-Z]{3}X[A-Z][0-9]{4}[A-Z]", p) for p in pans)
    assert all(p[3] not in specimen.VALID_PAN_HOLDER_TYPES for p in pans)


def test_upi_phone_pin_shapes():
    rng = py_rng(5, "misc")
    for _ in range(200):
        assert re.fullmatch(r"zz-specimen-[0-9a-f]{8}@[a-z]+", specimen.upi_id(rng))
        assert re.fullmatch(r"[6-9][0-9]{9}", specimen.phone_number(rng))
        assert re.fullmatch(r"[1-8][0-9]{5}", specimen.pin_code(rng))
    assert specimen.upi_id(rng, "specimen").endswith("@specimen")
    assert specimen.upi_payload("zz-specimen-1a2b3c4d@oksbi") == (
        "upi://pay?pa=zz-specimen-1a2b3c4d@oksbi&pn=SPECIMEN%20TEST&am=1&cu=INR"
    )


def test_forbidden_lint_catches_real_looking_numbers():
    # a Verhoeff-valid 12-digit number with leading 2-9 is forbidden
    body = "234567890123"[:11]
    real_like = body + str(verhoeff.check_digit(body))
    assert real_like[0] == "2"
    assert specimen.find_forbidden(f"id {real_like} x")
    assert specimen.find_forbidden(f"{real_like[:4]} {real_like[4:8]} {real_like[8:]}")
    # a PAN with a valid holder letter is forbidden, the X variant is not
    assert specimen.find_forbidden("ABCPE1234F")
    assert not specimen.find_forbidden("ABCXE1234F")
    # leading 0/1 numbers are fine, and hashes bounded by letters are ignored
    rng = py_rng(9, "ok")
    assert not specimen.find_forbidden(specimen.aadhaar_number(rng))
    assert not specimen.find_forbidden("a" + real_like + "b")


def test_scan_obj_walks_nested_structures():
    body = "345678901234"[:11]
    n = body + str(verhoeff.check_digit(body))
    assert specimen.scan_obj({"a": [{"b": n}]})
    assert not specimen.scan_obj({"a": [{"b": "hello"}], "c": 3})


def test_derive_seed_stable_and_distinct():
    assert derive_seed(1, "a") == derive_seed(1, "a")
    assert derive_seed(1, "a") != derive_seed(2, "a")
    assert derive_seed(1, "a") != derive_seed(1, "b")
