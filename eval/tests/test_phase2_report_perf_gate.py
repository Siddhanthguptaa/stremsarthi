"""Gate 2 report builder, the perf-leg analysis and scripts/gate_phase2.sh (arguments, plan, exit codes).

Nothing here starts a stream or loads a model: perf results are canned, the gate runs with ``--plan`` only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.harness.report import Check, Report, to_json_obj, to_markdown
from eval.phase2 import offline, perf
from eval.phase2 import report as p2report
from eval.phase2.detectors import MISSING, OK, DetectorStatus
from eval.phase2.rows import EXPECTED_DETECTORS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "scripts/gate_phase2.sh"


# ---------------------------------------------------------------- report.py: NOT RUN
def test_not_run_check_never_passes_and_is_shown_as_such():
    c = Check("x", None, ">=", 0.9, not_run=True, note="NOT RUN: detector missing")
    assert not c.passed and c.status == "NOT RUN"
    assert Check("y", 0.95, ">=", 0.9).status == "PASS" and Check("z", 0.5, ">=", 0.9).status == "FAIL"
    ok = Check("x", 1.0, ">=", 0.9, not_run=True)  # even a number cannot rescue a NOT RUN check
    assert not ok.passed
    rep = Report("t", "2026-11-21", checks=[c, Check("y", 0.95, ">=", 0.9)])
    md = to_markdown(rep)
    assert "Result: **FAIL**" in md and "NOT RUN" in md and "1 check(s) NOT RUN" in md
    js = to_json_obj(rep)
    assert js["checks"][0]["not_run"] is True and "not_run" not in js["checks"][1] and js["passed"] is False


def test_report_data_block_only_in_json_and_phase1_shape_unchanged():
    rep = Report("t", "2026-11-21", checks=[Check("a", 1.0, "==", 1.0)])
    assert "data" not in to_json_obj(rep)
    rep.data = {"rows": {"a": 1}}
    assert to_json_obj(rep)["data"] == {"rows": {"a": 1}} and "rows" not in to_markdown(rep)


# ---------------------------------------------------------------- the phase-2 report from leg files
def records(n=25):
    def t(i, cat, cov, level="clean"):
        return {
            "k": "t",
            "src": "img",
            "item": f"{cat}_{i}",
            "bucket": cat,
            "level": level,
            "frame": 0,
            "t": 0.0,
            "cat": cat,
            "j": 0,
            "cov": cov,
            "cov_cat": cov,
        }

    return [t(i, "aadhaar", 1.0) for i in range(n)] + [
        t(i, "pan", 0.0 if i < 5 else 1.0, "heavy" if i < 5 else "clean") for i in range(n)
    ]


def write_offline(tmp_path, det_missing=("plates",)):
    st = {
        i: DetectorStatus(i, MISSING if i in det_missing else OK, "no package", "accurate")
        for i in EXPECTED_DETECTORS
    }
    opts = offline.OfflineOptions(data=tmp_path, out=tmp_path)
    m = offline.build_measurements(
        records(),
        st,
        {"fast": [8.0, 9.0], "accurate": [120.0, 300.0]},
        {"cards": [100.0]},
        {"clip_frames": 0, "detect_errors": {}, "lane_runs": {"fast": 2}, "seconds": 1.2},
        opts,
        {"dataset": "tiny"},
    )
    checks = offline.analyse_offline(m, opts)
    m["failing_ids"] = offline.failing_ids(m, checks)
    offline._write_leg(opts, checks, m, "")
    return json.loads((tmp_path / "offline.json").read_text())


def test_phase2_report_lists_every_category_with_status_and_never_passes_missing_detectors(tmp_path):
    write_offline(tmp_path)
    rep = p2report.build_report(tmp_path, None, "2026-11-21")
    md, js = to_markdown(rep), to_json_obj(rep)
    assert not rep.passed
    rows = js["data"]["rows"]
    assert (
        rows["aadhaar"]["status"] == "PASS"
        and rows["pan"]["status"] == "FAIL"
        and rows["plate"]["status"] == "NOT RUN"
    )
    assert rows["plate"]["reason"].startswith("detector 'plates' missing")
    assert (
        rows["pan"]["levels"]["stills"]["heavy"]["recall"] == 0.0
        and rows["pan"]["levels"]["stills"]["clean"]["recall"] == 1.0
    )
    assert js["data"]["detectors"]["plates"] == "missing" and js["data"]["detectors"]["cards"] == "ok"
    assert "Per-category frame-level recall" in md and "Recall by difficulty level" in md
    assert "NOT RUN: Number plate" in md and "Latency per lane" in md
    assert js["failing_ids"] and all(i.startswith("pan:pan_") for i in js["failing_ids"])  # ids only


def test_report_json_is_stable_for_the_same_leg_files(tmp_path):
    write_offline(tmp_path)
    a = json.dumps(
        to_json_obj(p2report.build_report(tmp_path, None, "2026-11-21", extra_header={"git_rev": "x"})),
        sort_keys=True,
    )
    b = json.dumps(
        to_json_obj(p2report.build_report(tmp_path, None, "2026-11-21", extra_header={"git_rev": "x"})),
        sort_keys=True,
    )
    assert a == b


def test_gate_step_results_become_checks_and_a_failed_step_fails_the_report(tmp_path):
    write_offline(tmp_path, det_missing=())
    steps = {
        "steps": [
            {"name": "preflight", "command": "c", "rc": 0, "mapped_rc": 0, "seconds": 1.0, "note": ""},
            {"name": "perf (cpu)", "command": "c", "rc": 2, "mapped_rc": 2, "seconds": 2.0, "note": ""},
        ]
    }
    rep = p2report.build_report(tmp_path, steps, "2026-11-21")
    names = {c.name: c for c in rep.checks}
    assert names["step: preflight"].passed and not names["step: perf (cpu)"].passed


def test_baseline_is_written_only_by_a_passing_report(tmp_path, monkeypatch):
    write_offline(tmp_path)
    monkeypatch.setattr(p2report, "ROOT", tmp_path)
    rc = p2report.run(tmp_path, None, "2026-11-21", tmp_path / "rep")
    assert rc == 1 and not (tmp_path / "eval" / "baselines" / "phase2.json").exists()
    assert (tmp_path / "rep" / "2026-11-21-phase2.md").exists() and (
        tmp_path / "rep" / "2026-11-21-phase2.json"
    ).exists()


# ---------------------------------------------------------------- perf analysis (canned measurements)
def canned(fps=30.0, p95=4.6, cpu=3.2, recall=1.0, valid=0, protect=0.0):
    return {
        "delay_tap": {"n": 100, "p50_s": 4.4, "p95_s": p95, "max_s": p95 + 0.1},
        "delay_egress": {"n": 0, "p50_s": None, "p95_s": None, "max_s": None},
        "fps": {"fps": fps},
        "card": {
            "unreadable_recall": recall,
            "recall_ci": [0.99, 1.0],
            "visible_frames": 3000,
            "ocr_valid_reads": valid,
        },
        "stamp": {"stamp_unreadable_frac": 0.0},
        "protect_fraction": protect,
        "cpu": {"mean_cores": cpu, "peak_cores": cpu + 1},
        "vram_mb": {},
    }


def analyse(monkeypatch, m, started, gpu=False, baseline=4.5, real=True, vram=None):
    monkeypatch.setattr(perf, "common_measurements", lambda res: m)
    res = SimpleNamespace(engine_exit=None, spec=SimpleNamespace(gpu=gpu), vram_mb=vram or {})
    expected = ["cards", "faces", "plates", "screens", "text", "upi_qr"]
    return perf.analyse_perf(res, "gpu" if gpu else "cpu", started, expected, baseline, real)[0]


def names(checks):
    return {c.name: c for c in checks}


ALL = ["cards", "faces", "plates", "screens", "text", "upi_qr"]


def test_a_healthy_run_passes_every_check(monkeypatch):
    checks = analyse(monkeypatch, canned(), ALL)
    assert all(c.passed for c in checks), [c.name for c in checks if not c.passed]
    c = names(checks)
    assert c["all detectors on"].value == 6.0
    assert "[cpu] delay p95 vs the Phase 1 baseline (worse by <= 0.2 s)" in c


@pytest.mark.parametrize(
    ("kw", "failing"),
    [
        ({"fps": 24.0}, "[cpu] output fps (many_targets)"),
        ({"p95": 5.2}, "[cpu] delay p95 at tap"),
        ({"cpu": 5.6}, "[cpu] CPU, process tree mean"),
        ({"recall": 0.9}, "[cpu] card unreadable, frame recall"),
        ({"valid": 2}, "[cpu] Verhoeff-valid OCR reads of the card"),
        ({"protect": 0.3}, "[cpu] protect (slate) fraction while live"),
    ],
)
def test_each_criterion_can_fail_on_its_own(monkeypatch, kw, failing):
    c = names(analyse(monkeypatch, canned(**kw), ALL, baseline=None if kw.get("p95") else 4.6))
    assert not c[failing].passed
    others = [
        n
        for n, x in c.items()
        if n != failing and not x.passed and not x.not_run and "baseline" not in n and "delay" not in n
    ]
    assert not others, others


def test_a_detector_the_engine_did_not_start_fails_all_detectors_on(monkeypatch):
    c = names(analyse(monkeypatch, canned(), ["cards", "faces"]))["all detectors on"]
    assert not c.passed and "plates" in c.note and "upi_qr" in c.note


def test_no_vision_started_line_or_stand_in_engine_is_not_run(monkeypatch):
    assert names(analyse(monkeypatch, canned(), None))["all detectors on"].not_run
    assert names(analyse(monkeypatch, canned(), None, real=False))["all detectors on"].not_run


def test_delay_regression_against_the_phase1_baseline(monkeypatch):
    ok = names(analyse(monkeypatch, canned(p95=4.6), ALL, baseline=4.5))
    bad = names(analyse(monkeypatch, canned(p95=4.9), ALL, baseline=4.5))
    none = names(analyse(monkeypatch, canned(p95=4.9), ALL, baseline=None))
    key = "[cpu] delay p95 vs the Phase 1 baseline (worse by <= 0.2 s)"
    assert ok[key].passed and not bad[key].passed and "REGRESSED" in bad[key].note
    assert none[key].not_run and not none[key].passed  # no baseline yet: never a pass


def test_gpu_leg_checks_vram_growth_against_the_dev_cap(monkeypatch):
    base = {"peak_mb": 3000.0, "baseline_mb": 700.0, "peak_over_baseline_mb": 2300.0}
    ok = names(analyse(monkeypatch, canned(), ALL, gpu=True, vram=base))[
        "[gpu] VRAM growth over the idle baseline"
    ]
    big = names(analyse(monkeypatch, canned(), ALL, gpu=True, vram=base | {"peak_over_baseline_mb": 3900.0}))[
        "[gpu] VRAM growth over the idle baseline"
    ]
    none = names(analyse(monkeypatch, canned(), ALL, gpu=True, vram={}))[
        "[gpu] VRAM growth over the idle baseline"
    ]
    assert ok.passed and ok.target == 3500.0 and not big.passed and none.not_run


def test_detectors_started_parses_the_vision_log_line():
    log = "x\nvision started: lanes=['accurate', 'fast'] detectors=['cards', 'faces', 'upi_qr'] declared_vram_mb=700\ny"
    assert perf.detectors_started(log) == ["cards", "faces", "upi_qr"]
    assert perf.detectors_started("nothing here") is None


def test_baseline_delay_reads_the_phase1_baseline(tmp_path):
    p = tmp_path / "phase1.json"
    assert perf.baseline_delay("cpu", p) is None
    p.write_text(json.dumps({"main-cpu": {"delay_tap_p95_s": 4.4}}))
    assert perf.baseline_delay("cpu", p) == 4.4 and perf.baseline_delay("gpu", p) is None


# ---------------------------------------------------------------- scripts/gate_phase2.sh
def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(
        ["bash", SCRIPT, *args], cwd=ROOT, capture_output=True, text=True, env=e, timeout=60
    )


def steps(p):
    return [x for x in p.stdout.splitlines() if x.startswith("PLAN: [")]


def test_help_documents_the_targets_and_exit_codes():
    p = run("--help")
    assert p.returncode == 0, p.stderr
    for word in (
        "--offline-only",
        "--require-gpu",
        "--engine",
        "--gate-levels",
        "flock /tmp/streamsarthi-gpu.lock",
        "NOT RUN",
        "98 %",
        "<= 2 %",
        "5 cores",
        "Exit: 0 pass, 1 a criterion failed",
    ):
        assert word in p.stdout, word


@pytest.mark.parametrize(
    "args",
    [
        ["--nope"], ["extra"], ["--n"], ["--n", "0"], ["--n", "abc"], ["--clips", "-1"], ["--seed", "x"],
        ["--engine", "fake"], ["--perf-seconds", "soon"], ["--gate-levels", "clean,epic"], ["--gate-sources", "video"],
        ["--cpu-only", "--require-gpu"], ["--clips", "0"], ["--data"],
    ],
)  # fmt: skip
def test_bad_arguments_are_an_environment_error_with_a_hint(args):
    p = run(*args)
    assert p.returncode == 2, (args, p.stdout, p.stderr)
    assert "gate_phase2.sh:" in p.stderr and "--help" in p.stderr and "PLAN" not in p.stdout


def test_default_plan_has_dataset_preflight_offline_and_both_perf_legs():
    p = run("--plan")
    assert p.returncode == 0, p.stderr
    s = steps(p)
    assert [x.split("]")[0] for x in s] == [
        "PLAN: [dataset",
        "PLAN: [preflight",
        "PLAN: [offline accuracy (cpu)",
        "PLAN: [perf (cpu, all detectors)",
        "PLAN: [perf (gpu, all detectors)",
    ]
    assert "--n 40 --clips 4" in s[0] and "/tmp/ss_phase2_video " in s[0]
    gpu = s[4]
    assert (
        "flock -E 2 -w 1800 /tmp/streamsarthi-gpu.lock" in gpu
        and "SS_GPU_LOCK_HELD=1" in gpu
        and "(exit 3 accepted)" in gpu
    )
    assert "flock" not in s[3] and "--seconds 120" in s[3] and "--engine real" in s[3]
    assert "report -> eval/reports/<date>-phase2.md" in p.stdout


def test_options_reach_the_steps():
    s = steps(
        run(
            "--plan",
            "--quick",
            "--cpu-only",
            "--gate-levels",
            "clean,light",
            "--gate-sources",
            "img",
            "--engine",
            "ref",
            "--n",
            "12",
        )
    )
    off = next(x for x in s if "[offline" in x)
    assert "--gate-levels clean,light" in off and "--gate-sources img" in off and "--min-n 5" in off
    assert any("--seconds 45" in x for x in s) and "--n 12" in s[0] and "/tmp/ss_phase2_video_quick" in s[0]
    assert "not run: --cpu-only" in next(x for x in s if "[perf (gpu)" in x) and "flock" not in next(
        x for x in s if "[perf (gpu)" in x
    )
    assert "phase2-ref-quick" in run("--plan", "--quick", "--engine", "ref").stdout


def test_require_gpu_makes_a_missing_gpu_fatal_and_offline_only_skips_perf():
    gpu = next(x for x in steps(run("--plan", "--require-gpu")) if "perf (gpu" in x)
    assert "(exit 3 accepted)" not in gpu and "flock" in gpu
    s = steps(run("--plan", "--offline-only"))
    assert any("[perf (cpu)] (not run)" in x and "--offline-only" in x for x in s)
    assert "phase2-offline" in run("--plan", "--offline-only").stdout


def test_gate_lib_still_maps_the_exit_codes_used_here():
    script = 'source scripts/gate_lib.sh; for r in 0 1 2 3 127; do printf "%s " "$(gate_map_rc $r)"; done; gate_combine 0 3 1'
    p = subprocess.run(["bash", "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert p.stdout.split() == ["0", "1", "2", "3", "2", "1"]
