"""CPU tests of the video red team (eval/redteam/video): scoring, read-back, seeded broken detectors, determinism.

No detector models and no OCR model are loaded: the broken/perfect detectors are test doubles and the OCR is a fake.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np

from eval.data_gen.common import specimen
from eval.redteam.video import attacks, cli, pipeline, readback, score
from eval.redteam.video.model import Frame

ROOT = Path(__file__).resolve().parents[2]


def _case(attack: str, param: str):
    return next(c for c in attacks.cases(1234, attacks=[attack]) if c.param == param)


def _hash(frames: list[Frame]) -> str:
    h = hashlib.sha256()
    for f in frames:
        h.update(f.image.tobytes())
    return h.hexdigest()


# ------------------------------------------------------------------------------------------ catalogue
def test_catalogue_covers_every_required_attack() -> None:
    need = {
        "occlusion", "angle", "distance", "glare", "screens_of_screens", "photo_of_photo", "motion", "enter_leave",
        "flash", "fonts", "mixed_script", "split_lines", "separators", "face", "scene_cut", "frame_drops",
    }  # fmt: skip
    assert need <= set(attacks.GROUPS)
    assert len(attacks.cases(1234)) >= 60
    quick = attacks.cases(1234, quick=True)
    assert 0 < len(quick) < len(attacks.cases(1234))
    assert {c.attack for c in quick} == {
        c.attack for c in attacks.cases(1234) if not attacks.CATALOGUE[c.attack].requires()
    }


def test_case_ids_unique_and_selection_filters() -> None:
    ids = [c.id for c in attacks.cases(1234)]
    assert len(ids) == len(set(ids))
    assert {c.group for c in attacks.cases(1234, groups=["flash"])} == {"flash"}
    assert len(attacks.cases(1234, max_cases=3)) == 3


def test_cases_are_reproducible_and_seeded() -> None:
    a, b = _case("control", "qr").build(), _case("control", "qr").build()
    assert _hash(a) == _hash(b)
    other = next(c for c in attacks.cases(99, attacks=["control"]) if c.param == "qr")
    assert _hash(other.build()) != _hash(a)


def test_scenes_use_specimen_data_only() -> None:
    frames = _case("control", "aadhaar").build() + _case("control", "phone").build()
    assert specimen.scan_obj([t for f in frames for t in f.truth["targets"]]) == []


def test_frame_truth_marks_exposure_with_the_red_team_rule() -> None:
    fr = _case("enter_leave_card", "edge_hold35").build()
    t = next(t for t in fr[0].truth["targets"] if not t["aux"])
    assert 0.25 <= t["in_frame_fraction"] < 0.6
    assert t["visible"] is True  # exposed for the red team
    assert t["visible_spec"] is False  # not visible by the dataset rule (60 %)


def test_flash_case_hides_the_card_outside_its_window() -> None:
    fr = _case("flash_card", "f6").build()
    shown = [k for k, f in enumerate(fr) if any(t["visible"] for t in f.truth["targets"] if not t["aux"])]
    assert shown == list(range(22, 28))  # starts off the detect stride (22 % 3 == 1)


# ------------------------------------------------------------------------------------------ read-back
def test_norm_and_readable_handle_separators_zero_width_and_indic_digits() -> None:
    assert readback.norm("98765-43 210​") == "9876543210"
    assert readback.norm("९८७") == "987"  # Devanagari digits
    assert readback.readable(["9876543210"], "call 98765 43210 now")
    assert readback.readable(["9876543210"], "xx 987654 yy")  # a long enough run (6 of 10) is a leak
    assert not readback.readable(["9876543210"], "98 76 xx")
    assert not readback.readable(["9876543210"], "")
    assert readback.readable(["987654"], "9876")  # 6-digit PIN: 4 characters is enough to count
    assert readback.needed_run(12) == 8 and readback.needed_run(4) == 4


def test_paint_output_solid_hides_and_protect_flattens() -> None:
    img = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    m = pipeline.Mask("aadhaar", ((100, 100), (300, 100), (300, 200), (100, 200)), "t")
    out = readback.paint_output(img, [m], False, None)
    assert out[110:190, 110:290].std() < 3.0 and np.array_equal(out[400:500, 600:700], img[400:500, 600:700])
    flat = readback.paint_output(img, [], True, None)
    assert flat.std() == 0.0


# ------------------------------------------------------------------------------------------ seeded broken detectors
def _fake_ocr_by_sharpness(value: str):
    """Reads ``value`` from any region whose centre still has detail (a painted mask is flat there)."""

    def ocr(roi: np.ndarray) -> str:
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        h, w = g.shape
        centre = g[h // 5 : h - h // 5, w // 5 : w - w // 5]
        return value if centre.std() > 15 else ""

    return ocr


def _run(case, source, ocr=None):
    frames = case.build()
    return pipeline.run_case(
        case, source, readback.Reader(ocr=ocr or (lambda _i: "")), None, stride=1, frames=frames
    )


def test_blind_detector_leaks_a_qr_and_is_confirmed_by_decoding_the_output() -> None:
    case = _case("control", "qr")
    recs, _ = _run(case, pipeline.NullSource())
    s = score.summarise(recs)
    assert s["frames"]["rate"] == 1.0 and s["proxy"]["rate"] == 1.0
    assert s["confirmed"]["rate"] == 1.0  # zxing decodes the QR from the output frame


def test_perfect_detector_does_not_leak() -> None:
    case = _case("control", "qr")
    recs, _ = _run(case, pipeline.OracleSource())
    s = score.summarise(recs)
    assert s["frames"]["rate"] == 0.0 and s["confirmed"]["rate"] == 0.0


def test_displaced_and_partial_masks_are_flagged() -> None:
    case = _case("control", "aadhaar")
    for src in (pipeline.ShiftedSource(1.2), pipeline.PartialSource(0.5), pipeline.NullSource()):
        recs, _ = _run(case, src)
        assert score.summarise(recs)["proxy"]["rate"] == 1.0, type(src).__name__


def test_text_read_back_confirms_a_blind_detector_and_not_a_blur_that_works() -> None:
    case = _case("control", "phone")
    frames = case.build()
    value = next(t["value"] for t in frames[0].truth["targets"] if not t["aux"])
    ocr = _fake_ocr_by_sharpness(value)
    blind, _ = pipeline.run_case(
        case, pipeline.NullSource(), readback.Reader(ocr=ocr), None, stride=1, frames=frames
    )
    good, _ = pipeline.run_case(
        case, pipeline.OracleSource(), readback.Reader(ocr=ocr), None, stride=1, frames=frames
    )
    assert score.summarise(blind)["confirmed"]["rate"] == 1.0
    assert score.summarise(good)["frames"]["rate"] == 0.0


def test_proxy_leak_is_classified_by_source_legibility() -> None:
    case = _case("control", "phone")
    frames = case.build()
    blind, _ = pipeline.run_case(
        case, pipeline.NullSource(), readback.Reader(ocr=lambda _i: ""), None, stride=1, frames=frames
    )
    assert {r["ro"] for r in blind} == {"src_unreadable"}  # nothing readable even unmasked -> flagged as such
    ranked = score.rank_misses(blind)
    assert ranked[0]["kind"] == "proxy_unreadable"


def test_dropped_frames_are_not_scored_and_do_not_reach_the_source() -> None:
    case = _case("control", "qr")
    frames = case.build()
    for f in frames[::2]:
        f.dropped = True
    recs, info = pipeline.run_case(case, pipeline.NullSource(), None, None, frames=frames)
    assert info["dropped"] == len(frames[::2]) and len(recs) == len(frames) - len(frames[::2])


def test_selftest_of_the_cli_flags_the_seeded_defects() -> None:
    ok, lines = cli.selftest(1234)
    assert ok, lines


# ------------------------------------------------------------------------------------------ aggregation
def _rec(attack="a", param="p", group="g", proxy=True, ro=None, tag="") -> dict:
    r = {
        "case": f"{attack}.{param}",
        "attack": attack,
        "param": param,
        "group": group,
        "proxy": proxy,
        "frame": 0,
        "j": 0,
        "cat": "aadhaar",
        "cov": 0.0,
        "tag": tag,
    }
    if ro:
        r["ro"] = ro
    return r


def test_rate_math_and_ranking_put_confirmed_leaks_first() -> None:
    recs = (
        [_rec("x", "weak", proxy=True, ro="src_unreadable")] * 5
        + [_rec("y", "readable", proxy=True, ro="src_readable")] * 5
        + [_rec("z", "confirmed", proxy=True, ro="confirmed")] * 2
        + [_rec("z", "confirmed", proxy=False, ro="clean")] * 8
        + [_rec("ok", "fine", proxy=False, ro="clean")] * 10
    )
    s = score.summarise(recs)
    assert s["frames"]["k"] == 12 and s["frames"]["n"] == 30
    ranked = score.rank_misses(recs)
    assert [m["attack"] for m in ranked] == ["z", "y", "x"]
    assert [m["kind"] for m in ranked] == ["confirmed", "proxy_readable", "proxy_unreadable"]
    assert all(m["attack"] != "ok" for m in ranked)
    assert score.by_group(recs)["g"]["frames"]["n"] == 30


def test_regression_check_flags_more_than_five_points() -> None:
    base = {"groups": {"flash": 0.10}, "attacks": {"flash_card": 0.10}}
    assert score.regressions({"groups": {"flash": 0.14}, "attacks": {"flash_card": 0.14}}, base) == []
    bad = score.regressions({"groups": {"flash": 0.20}, "attacks": {"flash_card": 0.12}}, base)
    assert len(bad) == 1 and "flash" in bad[0]


# ------------------------------------------------------------------------------------------ wiring
def test_cli_list_and_eval_run_forward() -> None:
    from eval import run as eval_run

    assert cli.main(["--list", "--quick"]) == 0
    assert eval_run.main(["redteam-video", "--list", "--attacks", "flash_card"]) == 0


def test_gate_script_is_lf_and_maps_exit_codes() -> None:
    p = ROOT / "scripts" / "gate_redteam_video.sh"
    raw = p.read_bytes()
    assert b"\r" not in raw and raw.startswith(b"#!/usr/bin/env bash")
    text = raw.decode()
    assert "--selftest" in text and "redteam-video" in text and "exit 2" in text
