"""Gate 5 harness and guard data tests (CPU only, a quarter-size set built once per module)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from eval import phase5, phase5_ref, phase5_report, phase5_shaping
from eval.data_gen.common.specimen import scan_obj
from eval.data_gen.guard import profanity as prof
from eval.data_gen.guard.songs import make_song
from eval.data_gen.guard.suite import build_suite, load, load_utterances, manifest_hash

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def quick(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("guard_quick")
    build_suite(out, 1234, quick=True)
    return out


def test_songs_are_deterministic_and_distinct() -> None:
    a, b, c = make_song(1, 4.0), make_song(1, 4.0), make_song(2, 4.0)
    assert np.array_equal(a, b)
    assert not np.allclose(a, c)
    assert a.dtype == np.float32 and abs(float(np.max(np.abs(a))) - 0.8) < 1e-3


def test_suite_is_deterministic_and_clean(quick: Path, tmp_path: Path) -> None:
    m1 = load(quick)
    m2 = build_suite(tmp_path / "again", 1234, quick=True)
    assert manifest_hash(m1) == manifest_hash(m2)
    m3 = build_suite(tmp_path / "other", 99, quick=True)
    assert manifest_hash(m3) != manifest_hash(m1)
    assert not scan_obj(m1)
    kinds = m1["totals"]["clips_by_kind"]
    assert {
        "music_only",
        "music_under_speech",
        "speech_only",
        "fp_positive",
        "fp_negative",
        "fp_tempo",
    } <= set(kinds)
    for c in m1["clips"] + m1["snippets"]:
        assert (quick / c["file"]).is_file()


def test_fingerprint_snippets_are_five_seconds(quick: Path) -> None:
    for s in load(quick)["snippets"]:
        if s["kind"] != "fp_tempo":
            assert (quick / s["file"]).stat().st_size >= 5 * 16000 * 2


def test_profanity_truth_is_consistent() -> None:
    utts = prof.build_utterances(7)
    assert len({u.text for u in utts}) == len(utts)
    groups = {u.group for u in utts}
    assert {
        "hinglish_latn",
        "hinglish_deva",
        "english",
        "variants",
        "regional",
        "clean",
        "clean_trap",
    } <= groups
    for u in utts:
        if u.profane:
            assert u.hit_words, u.id
            assert " ".join(u.words[i][0] for i in u.hit_words) in u.text
        else:
            assert not u.hit_words and u.level == 0
        starts = [w[1] for w in u.words]
        assert starts == sorted(starts)
    assert [u.id for u in utts] == [u.id for u in prof.build_utterances(7)]
    assert (
        any(u.group == "hinglish_deva" for u in utts) and sum(u.group == "hinglish_latn" for u in utts) >= 60
    )


def test_quick_utterances_roundtrip(quick: Path) -> None:
    utts = load_utterances(quick)
    assert utts and all(isinstance(u.words[0], tuple) for u in utts)


@pytest.mark.parametrize("leg", [ln for ln in phase5.LEGS if ln != "data"])
def test_reference_stand_ins_pass(quick: Path, tmp_path: Path, leg: str) -> None:
    assert phase5.main(["--leg", leg, "--data", str(quick), "--out", str(tmp_path), "--reference"]) == 0


@pytest.mark.parametrize(
    ("defect", "leg"),
    [
        ("music_blind", "music"), ("fp_blind", "fingerprint"), ("slow_meter", "meter"), ("no_deva_profanity", "profanity"),
        ("late_captions", "captions_timing"), ("basic_shaping", "captions_render"),
    ],
)  # fmt: skip
def test_each_seeded_defect_fails_its_leg(quick: Path, tmp_path: Path, defect: str, leg: str) -> None:
    rc = phase5.main(
        ["--leg", leg, "--data", str(quick), "--out", str(tmp_path), "--reference", "--defect", defect]
    )
    assert rc == 1, defect


def test_defect_without_reference_is_an_error(quick: Path, tmp_path: Path) -> None:
    assert (
        phase5.main(
            ["--leg", "music", "--data", str(quick), "--out", str(tmp_path), "--defect", "music_blind"]
        )
        == 2
    )
    assert (
        phase5.main(
            [
                "--leg",
                "music",
                "--data",
                str(quick),
                "--out",
                str(tmp_path),
                "--reference",
                "--defect",
                "nope",
            ]
        )
        == 2
    )


def test_missing_data_is_an_environment_error(tmp_path: Path) -> None:
    assert (
        phase5.main(["--leg", "music", "--data", str(tmp_path / "nothing"), "--out", str(tmp_path / "o")])
        == 2
    )
    assert (
        phase5.main(["--leg", "data", "--data", str(tmp_path / "nothing"), "--out", str(tmp_path / "o")]) == 2
    )


def test_missing_component_is_not_run_never_a_pass(
    quick: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "guard.risk", None)  # ImportError on import
    rc = phase5.main(["--leg", "profanity", "--data", str(quick), "--out", str(tmp_path)])
    assert rc == 2
    res = json.loads((tmp_path / "phase5-profanity.json").read_text(encoding="utf-8"))
    assert "not_run" in res
    checks = phase5_report.evaluate(res)
    assert checks and all(c.not_run and not c.passed for c in checks)


def test_evaluate_thresholds_on_toy_numbers() -> None:
    def r(v: float) -> dict:
        return {"value": v}

    good = {"leg": "music", "recall": r(0.91), "false_positive": r(0.05)}
    assert all(c.passed for c in phase5_report.evaluate(good))
    assert not all(c.passed for c in phase5_report.evaluate({**good, "recall": r(0.89)}))
    assert not all(c.passed for c in phase5_report.evaluate({**good, "false_positive": r(0.06)}))
    meter = {"leg": "meter", "reaction_s": {"max": 2.0}, "level_ok": r(1.0), "publish_hz": 2.0}
    assert all(c.passed for c in phase5_report.evaluate(meter))
    assert not all(c.passed for c in phase5_report.evaluate({**meter, "reaction_s": {"max": 2.1}}))
    assert not all(
        c.passed for c in phase5_report.evaluate({**meter, "reaction_s": {"max": None}})
    )  # never reacted
    assert not all(c.passed for c in phase5_report.evaluate({**meter, "publish_hz": 2.5}))


def test_shaping_reference_matches_itself_and_basic_layout_is_caught() -> None:
    text = "क्षमा करें श्रीमान् ज्ञान और प्रश्न"
    good = phase5_shaping.compare(phase5_shaping.ref_mask(text, "hi", True), text, "hi")
    assert good["ok"] and good["raqm_differs_from_basic"]
    bad = phase5_shaping.compare(phase5_shaping.ref_mask(text, "hi", False), text, "hi")
    assert not bad["ok"]
    assert not phase5_shaping.compare(np.zeros((40, 200), np.uint8), text, "hi")["ok"]  # no ink


def test_reference_meter_matches_the_spec_examples() -> None:
    r = phase5_ref.RefRisk()
    r.add("music", 0.8, 10.0, 0.0)
    assert round(r.score(0.0), 3) == 0.28 and r.meter(0.0)[0] == "yellow"
    r = phase5_ref.RefRisk()
    r.add("fingerprint", 0.95, 60.0, 0.0)
    assert r.meter(0.0)[0] == "red"


def test_gate_script_is_lf_and_plans() -> None:
    script = ROOT / "scripts" / "gate_phase5.sh"
    assert b"\r" not in script.read_bytes()
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("bash not available")
    p = subprocess.run(
        [bash, str(script), "--plan"], capture_output=True, text=True, cwd=ROOT, timeout=60, check=False
    )
    assert p.returncode == 0, p.stderr
    for name in (
        "data check",
        "music detection",
        "fingerprint",
        "profanity",
        "risk meter",
        "captions render",
        "captions timing",
    ):
        assert f"[{name}]" in p.stdout
    bad = subprocess.run(
        [bash, str(script), "--defect", "music_blind"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )
    assert bad.returncode == 2
