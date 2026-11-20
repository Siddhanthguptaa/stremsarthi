"""Video suite (T2.1): determinism, ground-truth geometry, augmentation limits, SPECIMEN lint, CLI."""

import json
from pathlib import Path

import numpy as np
import pytest
import zxingcpp

from eval.data_gen.common import identifiers, specimen, verhoeff
from eval.data_gen.video import augment, cli, suite
from eval.data_gen.video import faces as faces_mod
from eval.sender.stamp import STAMP_EXCLUSION

ROOT = Path(__file__).resolve().parents[2]
FACES_CACHE = ROOT / suite.DEFAULT_FACES_CACHE
HAVE_FACES = not faces_mod.cache_problems(FACES_CACHE)
NON_FACE = tuple(b for b in suite.BUCKETS if not suite.SPECS[b].needs_faces)
CTX = suite.Ctx(str(FACES_CACHE))


def _truth_quads(truth):
    for key in ("targets", "distractors", "context"):
        for rec in truth[key]:
            yield key, rec


def _inside(quad, margin=0.0):
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return min(xs) >= -margin and min(ys) >= -margin and max(xs) <= 1280 + margin and max(ys) <= 720 + margin


def test_bucket_list_covers_every_phase2_category():
    names = set(suite.BUCKETS)
    assert {
        "aadhaar",
        "pan",
        "upi_qr",
        "qr_negative",
        "plate",
        "otp_screen",
        "game_bgmi",
        "game_freefire",
    } <= names
    assert {
        f"signboard_{s}" for s in ("devanagari", "kannada", "tamil", "telugu", "bengali", "latin")
    } <= names
    assert {f"text_{k}" for k in ("phone", "pin", "upi_id", "email")} <= names
    assert {
        "faces_multi",
        "faces_enrolled",
        "faces_neg",
        "neg_card",
        "neg_plate",
        "neg_text",
        "neg_screen",
        "neg_game",
    } <= names


@pytest.mark.parametrize("bucket", NON_FACE)
def test_items_are_deterministic_and_seed_sensitive(bucket):
    a_img, a_truth = suite.build_item(bucket, 3, 1234, CTX)
    b_img, b_truth = suite.build_item(bucket, 3, 1234, CTX)
    c_img, _ = suite.build_item(bucket, 3, 99, CTX)
    assert np.array_equal(a_img, b_img) and a_truth == b_truth
    assert not np.array_equal(a_img, c_img)
    assert a_img.shape == (720, 1280, 3) and a_img.dtype == np.uint8


@pytest.mark.parametrize("bucket", NON_FACE)
def test_ground_truth_geometry(bucket):
    sx1, sy1, sx2, sy2 = STAMP_EXCLUSION
    for index in range(6):
        _, truth = suite.build_item(bucket, index, 1234, CTX)
        assert truth["category"] == bucket and truth["specimen"] is True
        assert truth["level"] == augment.level_for(index)
        for _key, rec in _truth_quads(truth):
            assert len(rec["quad"]) == 4 and _inside(rec["quad"], margin=1.5), (
                bucket,
                index,
                rec["category"],
            )
            xs, ys = [p[0] for p in rec["quad"]], [p[1] for p in rec["quad"]]
            if _key != "context":  # whole-screen context quads legitimately cover the stamp corner
                assert max(xs) < sx1 or min(xs) > sx2 or max(ys) < sy1 or min(ys) > sy2  # never over the stamp
            assert 0.0 <= rec["occlusion_fraction"] <= 1.0 and rec["in_frame_fraction"] >= 0.95
            assert rec["visible"] == (rec["in_frame_fraction"] >= 0.6 and rec["min_side_px"] >= 24)
        assert truth["n_visible_targets"] == sum(
            1 for t in truth["targets"] if t["visible"] and not t.get("aux")
        )


def test_positive_buckets_have_targets_and_negative_buckets_have_none():
    for bucket in NON_FACE:
        spec = suite.SPECS[bucket]
        _, truth = suite.build_item(bucket, 1, 1234, CTX)
        main = [t for t in truth["targets"] if not t.get("aux")]
        if bucket == "qr_negative" or spec.group == "negative":
            assert not main, bucket
            assert truth["distractors"], bucket
        else:
            assert main, bucket


def test_augmentation_limits_and_level_mix():
    levels = [augment.level_for(i) for i in range(100)]
    assert levels.count("clean") == 20 and levels.count("light") == 40 and levels.count("heavy") == 40
    import random

    seen_heavy = []
    for seed in range(300):
        for level in ("clean", "light", "heavy"):
            p = augment.sample_params(random.Random(seed), level)
            assert max(abs(p.roll), abs(p.yaw), abs(p.pitch)) <= augment.MAX_TILT_DEG
            assert 0.0 <= p.occlusion_target <= 0.40 and p.gain <= 1.06
            if level == "heavy":
                seen_heavy.append(p)
            if level == "clean":
                assert p.blur_kind == "none" and not p.glare and p.occlusion_target == 0
    assert any(p.glare for p in seen_heavy) and any(p.blur_kind == "motion" for p in seen_heavy)
    assert any(p.blur_kind in ("defocus", "both") for p in seen_heavy) and any(
        p.occlusion_target > 0.3 for p in seen_heavy
    )
    assert max(abs(p.roll) for p in seen_heavy) > 20  # tilt reaches the 25 degree regime


def test_heavy_augmentation_changes_pixels_and_records_parameters():
    # heavy items are blurred / dimmed / occluded: they differ from the clean render of the same sprite
    _, truth = suite.build_item("aadhaar", 3, 1234, CTX)  # index 3 -> heavy
    aug = truth["aug"]
    assert (
        truth["level"] == "heavy" and aug["glare"] and aug["blur_kind"] != "none" and aug["jpeg_quality"] > 0
    )


def test_measured_occlusion_tracks_requested_fraction():
    found = []
    for i in range(3, 40, 5):
        _, truth = suite.build_item("pan", i, 7, CTX)
        want = truth["aug"]["occlusion_target"]
        main = next(t for t in truth["targets"] if not t.get("aux"))
        if want > 0.05:
            found.append((want, main["occlusion_fraction"]))
    assert found
    for want, got in found:
        assert 0.3 * want <= got <= 1.8 * want + 0.05, (want, got)


def test_clean_upi_qr_decode_to_ground_truth():
    ok = total = 0
    for i in range(0, 60, 5):  # clean level only
        img, truth = suite.build_item("upi_qr", i, 1234, CTX)
        assert truth["level"] == "clean"
        payload = truth["targets"][0]["payload"]
        total += 1
        ok += payload in [r.text for r in zxingcpp.read_barcodes(img)]
        assert payload.startswith("upi://pay?pa=zz-specimen-")
    assert ok / total >= 0.9, (ok, total)


def test_clean_negative_qr_is_not_upi():
    for i in range(0, 30, 5):
        img, truth = suite.build_item("qr_negative", i, 1234, CTX)
        texts = [r.text for r in zxingcpp.read_barcodes(img)]
        assert not any(t.lower().startswith("upi://") for t in texts)
        assert truth["distractors"][0]["payload"] in texts or truth["level"] != "clean"


def test_card_numbers_follow_specimen_rules():
    for i in range(10):
        _, a = suite.build_item("aadhaar", i, 1234, CTX)
        digits = a["targets"][0]["number"].replace(" ", "")
        assert len(digits) == 12 and verhoeff.is_valid(digits) and digits[0] in "01"
        _, p = suite.build_item("pan", i, 1234, CTX)
        assert len(p["targets"][0]["number"]) == 10 and p["targets"][0]["number"][3] == "X"


def test_plate_and_text_values_follow_format_rules():
    for i in range(10):
        _, t = suite.build_item("plate", i, 1234, CTX)
        plate = t["targets"][0]
        assert identifiers.PLATE_STANDARD_RE.fullmatch(plate["text"]) or identifiers.PLATE_BH_RE.fullmatch(
            plate["text"]
        )
        assert plate["layout"] in ("one_line", "two_line")
        _, ph = suite.build_item("text_phone", i, 1234, CTX)
        for tg in ph["targets"]:
            assert identifiers.PHONE_RE.search(identifiers.from_devanagari_digits(tg["value"]))
        _, pn = suite.build_item("text_pin", i, 1234, CTX)
        for tg in pn["targets"]:
            assert (
                identifiers.PIN_RE.search(identifiers.from_devanagari_digits(tg["value"]))
                and tg["cue"] is True
            )
        _, up = suite.build_item("text_upi_id", i, 1234, CTX)
        for tg in up["targets"]:
            assert identifiers.UPI_ID_RE.search(tg["value"]) and "zz-specimen-" in tg["value"]
        _, em = suite.build_item("text_email", i, 1234, CTX)
        for tg in em["targets"]:
            assert identifiers.EMAIL_RE.fullmatch(tg["value"]) and tg["value"].endswith(".invalid")
        _, otp = suite.build_item("otp_screen", i, 1234, CTX)
        banner = [t for t in otp["targets"] if t["category"] == "otp_banner"]
        assert banner and 4 <= banner[0]["code_digits"] <= 8
        _, gm = suite.build_item("game_bgmi", i, 1234, CTX)
        cats = {t["category"]: t["value"] for t in gm["targets"]}
        assert (
            8 <= len(cats["game_player_id"]) <= 12
            and 4 <= len(cats["game_room_id"]) <= 8
            and 4 <= len(cats["game_room_password"]) <= 8
        )


def test_signboard_buckets_carry_their_script():
    for script in ("devanagari", "kannada", "tamil", "telugu", "bengali", "latin"):
        _, truth = suite.build_item(f"signboard_{script}", 2, 1234, CTX)
        scripts = {t["script"] for t in truth["targets"]}
        assert script in scripts and scripts <= {script, "latin"}


def test_suite_manifest_deterministic_by_seed_and_clean_of_forbidden_patterns():
    subset = ("aadhaar", "pan", "plate", "text_phone", "otp_screen", "neg_card")
    a = suite.build_suite(None, 1234, 4, subset, workers=1, faces_cache=str(FACES_CACHE))
    b = suite.build_suite(None, 1234, 4, subset, workers=1, faces_cache=str(FACES_CACHE))
    c = suite.build_suite(None, 4321, 4, subset, workers=1, faces_cache=str(FACES_CACHE))
    assert a["manifest_sha256"] == b["manifest_sha256"] != c["manifest_sha256"]
    assert [i["pixel_sha256"] for i in a["items"]] == [i["pixel_sha256"] for i in b["items"]]
    assert specimen.scan_obj({k: v for k, v in a.items() if not k.startswith("_")}) == []
    assert a["generator_version"] == suite.GENERATOR_VERSION and len(a["items"]) == 24


def test_forbidden_scan_over_many_items_of_every_bucket():
    for bucket in NON_FACE:
        for i in range(8):
            _, truth = suite.build_item(bucket, i, 2024, CTX)
            assert specimen.scan_obj(truth) == [], (bucket, i)


def test_parallel_and_serial_builds_agree():
    subset = ("pan", "signboard_tamil", "neg_text")
    a = suite.build_suite(None, 5, 6, subset, workers=1, faces_cache=str(FACES_CACHE))
    b = suite.build_suite(None, 5, 6, subset, workers=3, faces_cache=str(FACES_CACHE))
    assert a["manifest_sha256"] == b["manifest_sha256"]


def test_cli_writes_images_truth_files_and_manifest(tmp_path):
    out = tmp_path / "video"
    rc = cli.main(
        [
            "--out",
            str(out),
            "--seed",
            "7",
            "--n",
            "2",
            "--buckets",
            "pan",
            "signboard_kannada",
            "--workers",
            "1",
        ]
    )
    assert rc == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["seed"] == 7 and len(manifest["items"]) == 4
    for item in manifest["items"]:
        assert (out / item["file"]).stat().st_size > 1000
        truth = json.loads((out / item["truth_file"]).read_text())
        assert truth == item["truth"] and truth["targets"]


@pytest.mark.skipif(not HAVE_FACES, reason="portraits not cached (run gen-video once with network)")
def test_face_buckets_have_expected_truth():
    for i in range(6):
        _, multi = suite.build_item("faces_multi", i, 1234, CTX)
        faces = [t for t in multi["targets"] if t["category"] == "face"]
        assert 2 <= len(faces) <= 5 and not any(t["allowlisted"] for t in faces)
        assert len({t["person_id"] for t in faces}) == len(faces)
        _, enr = suite.build_item("faces_enrolled", i, 1234, CTX)
        flags = [t["allowlisted"] for t in enr["targets"]]
        assert any(flags) and all(
            (t["person_id"] in faces_mod.ENROLLED_PERSONS) == t["allowlisted"] for t in enr["targets"]
        )
        _, neg = suite.build_item("faces_neg", i, 1234, CTX)
        assert not neg["targets"] and neg["distractors"]
        for _k, rec in _truth_quads(multi):
            assert _inside(rec["quad"], margin=1.5)
