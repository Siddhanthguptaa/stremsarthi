"""T7.6: the final report (eval/final_report.py) and scripts/gate_final.sh. CPU only, canned reports, no gate is run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from eval import final_report as fr
from eval import run as eval_run

ROOT = Path(__file__).resolve().parents[2]
TODAY = "2026-11-21"
STATUS = (
    "| G0 | `scripts/gate_phase0.sh` | PASS (exit 0) | 2026-11-21, run by the lead |\n"
    "| G3 | `scripts/gate_phase3.sh` | PASS | builder-reported, ORACLE |\n"
    "| G6 | `scripts/gate_phase6.sh` | pending | |\n"
)


def chk(name: str, op: str, target: float, value: float, ok: bool = True, **kw) -> dict:
    return {
        "name": name,
        "op": op,
        "target": target,
        "value": value,
        "passed": ok,
        "unit": "",
        "note": "",
        **kw,
    }


def put(d: Path, name: str, checks: list[dict], **extra) -> Path:
    p = d / f"{TODAY}-{name}.json"
    p.write_text(
        json.dumps(
            {"phase": name, "date": TODAY, "checks": checks, "header": {"git_rev": "abc1234"}, **extra}
        ),
        encoding="utf-8",
    )
    return p


G3_OK = [
    chk("mute coverage, PII words (duration-weighted)", ">=", 0.9, 0.95),
    chk("mute coverage, spoken digit words", ">=", 0.95, 0.98),
    chk("over-mute of non-PII speech", "<=", 0.15, 0.14),
    chk("audio pipeline latency p95", "<=", 3.3, 2.5),
]


def build(d: Path, **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("status_md", STATUS)
    kw.setdefault("root", d)  # no baseline, no docs/COST.md under tmp
    return fr.build(d, **kw)


def rows(rep, gate, item_part=""):
    return [r for r in rep["rows"] if r["gate"] == gate and item_part in r["item"]]


def test_missing_report_is_not_run_and_never_a_pass(tmp_path):
    put(tmp_path, "phase4", [chk("abuse recall", ">=", 0.85, 1.0)])  # only G4 has a report
    rep = build(tmp_path)
    g3 = rows(rep, "G3")
    assert g3 and all(r["verdict"] == "NOT RUN" for r in g3)
    assert all("NOT RUN" in r["caveats"] for r in g3)
    assert rows(rep, "G4", "abuse recall")[0]["verdict"] == "PASS"
    assert rep["gates"]["G3"]["verdict"] == "NOT RUN"
    assert rep["gates"]["G6"]["verdict"] == "NOT RUN"
    assert rep["overall"].startswith("INCOMPLETE") and rep["exit_code"] == 3
    assert any(x.startswith("G3:") for x in rep["not_run"])


def test_failing_gate_shows_fail_and_exit_1(tmp_path):
    bad = G3_OK[:3] + [chk("audio pipeline latency p95", "<=", 3.3, 4.1, ok=False)]
    put(tmp_path, "phase3", bad)
    rep = build(tmp_path)
    r = rows(rep, "G3", "audio pipeline")[0]
    assert r["verdict"] == "FAIL" and r["measured"] == "4.1"
    assert rep["gates"]["G3"]["verdict"] == "FAIL"
    assert rep["exit_code"] == 1 and rep["overall"] == "FAIL"


def test_not_run_check_inside_a_report_is_not_run(tmp_path):
    put(
        tmp_path,
        "phase3",
        G3_OK[:3] + [chk("audio pipeline latency p95", "<=", 3.3, 0.0, ok=False, not_run=True)],
    )
    rep = build(tmp_path)
    assert rows(rep, "G3", "audio pipeline")[0]["verdict"] == "NOT RUN"
    assert rep["gates"]["G3"]["verdict"] == "NOT RUN"


def test_stale_report_is_never_a_pass(tmp_path):
    p = put(tmp_path, "phase3", G3_OK)
    old = time.time() - 3600
    os.utime(p, (old, old))
    rep = build(tmp_path, since=time.time() - 60)  # this run started after the report was written
    g3 = rows(rep, "G3", "mute coverage, PII")
    assert g3[0]["verdict"] == "STALE" and "before this run" in g3[0]["caveats"]
    assert rep["gates"]["G3"]["verdict"] == "STALE"
    assert rep["exit_code"] == 3
    # and by date: a report 30 days old
    rep2 = fr.build(tmp_path, today="2026-12-21", status_md=STATUS, root=tmp_path, max_age_days=7)
    assert rows(rep2, "G3", "mute coverage, PII")[0]["verdict"] == "STALE"


def test_nothing_is_a_pass_even_with_every_automated_gate_present(tmp_path):
    put(tmp_path, "phase0", [chk("doctor", "==", 0, 0)])
    put(tmp_path, "phase3", G3_OK)
    rep = build(tmp_path)
    assert rep["exit_code"] != 0  # manual items (RTX 5050 runs, netem) and G6 stay NOT RUN
    assert any("RTX 5050" in x for x in rep["not_run"])
    assert any(x.startswith("G6:") for x in rep["not_run"])


def test_caveats_come_from_status_md_conventions(tmp_path):
    put(tmp_path, "phase0", [chk("doctor", "==", 0, 0)])
    put(tmp_path, "phase3", G3_OK)
    rep = build(tmp_path)
    c3 = rows(rep, "G3", "mute coverage, PII")[0]["caveats"]
    assert "SYNTHETIC" in c3 and "ORACLE" in c3 and "BUILDER-REPORTED" in c3
    assert "LEAD-RUN" in rows(rep, "G0", "doctor")[0]["caveats"]
    assert "STATUS.md: pending" in fr.status_notes(STATUS)["G6"]


def test_g2_level_scope_and_quick_marking(tmp_path):
    chk2 = [
        chk("offline: Aadhaar card recall [stills]", ">=", 0.95, 1.0),
        chk("offline: Aadhaar card recall [clips]", ">=", 0.95, 0.9, ok=False),
    ]
    put(tmp_path, "phase2-offline-d48", chk2, banner="Gate scope: clean+light (D-48); heavy is not gated")
    rep = build(tmp_path)
    r = rows(rep, "G2", "Aadhaar")
    assert len(r) == 2 and {x["verdict"] for x in r} == {"PASS", "FAIL"}
    assert all("clean+light (D-48)" in x["scope"] for x in r)
    assert all("offline leg only" in x["caveats"] for x in r)
    assert all(x["verdict"] == "NOT RUN" for x in rows(rep, "G2", "perf:"))


def test_other_failing_checks_roll_up_so_they_cannot_hide(tmp_path):
    put(
        tmp_path,
        "phase4",
        [chk("abuse recall", ">=", 0.85, 1.0), chk("Twitch: !hold from a viewer", "==", 0, 1, ok=False)],
    )
    rep = build(tmp_path)
    assert rows(rep, "G4", "Twitch")[0]["verdict"] == "FAIL"
    assert rep["gates"]["G4"]["verdict"] == "FAIL"
    put(
        tmp_path,
        "phase5",
        [chk("music detect recall (x)", ">=", 0.9, 1.0), chk("brand new check", "==", 0, 1, ok=False)],
    )
    rep = build(tmp_path)
    other = rows(rep, "G5", "other checks")[0]
    assert other["verdict"] == "FAIL" and "brand new check" in other["measured"]


def test_orchestrator_results_can_only_make_a_gate_worse(tmp_path):
    put(tmp_path, "phase3", G3_OK)
    res = {
        "steps": [
            {"name": "G3", "rc": 1, "mapped_rc": 1, "note": ""},
            {"name": "G6", "rc": 3, "mapped_rc": 3, "note": "NOT RUN: gone"},
        ]
    }
    rep = build(tmp_path, results=res)
    assert rep["gates"]["G3"]["verdict"] == "FAIL" and rep["exit_code"] == 1
    res = {"steps": [{"name": "G3", "rc": 3, "mapped_rc": 3, "note": "NOT RUN: skipped by --skip"}]}
    rep = build(tmp_path, results=res)
    assert rep["gates"]["G3"]["verdict"] == "NOT RUN"  # a fresh-looking report does not rescue a skipped gate
    res = {"steps": [{"name": "e2e", "rc": 0, "mapped_rc": 0, "note": ""}]}
    assert rows(build(tmp_path, results=res), "E2E")[0]["verdict"] == "PASS"
    assert rows(build(tmp_path), "E2E")[0]["verdict"] == "NOT RUN"


def test_verified_live_is_empty_unless_a_live_report_exists(tmp_path):
    put(tmp_path, "phase3", G3_OK)
    rep = build(tmp_path)
    assert rep["verified_live"] == []
    md = fr.to_markdown(rep)
    assert "## What is verified live" in md and "nothing is verified live" in md
    put(
        tmp_path,
        "live-youtube",
        [chk("egress connected", ">=", 1, 1), chk("no reconnect", "==", 0, 2, ok=False)],
    )
    rep = build(tmp_path)
    assert [v["check"] for v in rep["verified_live"]] == ["egress connected"]  # only checks that passed


def test_video_red_team_rows_and_regression(tmp_path):
    groups = {
        g: {"frames": {"rate": 0.1, "k": 1, "n": 10, "ci": [0.0, 0.4]}}
        for g in ("angle", "glare", "occlusion")
    }
    put(tmp_path, "redteam-video", [], groups=groups, regressions=[])
    rep = build(tmp_path)
    assert rows(rep, "RT-V", "angle")[0]["verdict"] == "RECORDED"
    assert rows(rep, "RT-V", "motion")[0]["verdict"] == "NOT RUN"  # group absent from the report
    base = tmp_path / "eval" / "baselines"
    base.mkdir(parents=True)
    (base / "redteam_video.json").write_text("{}")
    put(tmp_path, "redteam-video", [], groups=groups, regressions=[{"attack": "angle"}])
    rep = build(tmp_path)
    assert rows(rep, "RT-V", "regression")[0]["verdict"] == "FAIL"


def test_write_and_cli_forwarder(tmp_path, capsys):
    put(tmp_path, "phase3", G3_OK)
    rc = eval_run.main(
        [
            "final-report",
            "--report-dir",
            str(tmp_path),
            "--date",
            TODAY,
            "--status",
            str(tmp_path / "none.md"),
        ]
    )
    assert rc == 3  # incomplete, never 0
    md, js = tmp_path / f"{TODAY}-final.md", tmp_path / f"{TODAY}-final.json"
    assert md.exists() and js.exists()
    data = json.loads(js.read_text())
    assert data["cost_pointer"] == "docs/COST.md" and data["rows"]
    text = md.read_text()
    assert "NOT RUN" in text and "docs/COST.md" in text and "caveats" in text


# ---------------------------------------------------------------- scripts/gate_final.sh (stubbed gates)


def _bash() -> str | None:
    b = shutil.which("bash")
    if not b or sys.platform == "win32":
        return None
    return b


needs_bash = pytest.mark.skipif(_bash() is None, reason="needs POSIX bash (run inside WSL)")


def test_gate_final_script_is_lf_only_and_has_the_options():
    raw = (ROOT / "scripts" / "gate_final.sh").read_bytes()
    assert b"\r" not in raw
    text = raw.decode()
    for opt in ("--quick", "--plan", "--skip", "--report-dir"):
        assert opt in text


@needs_bash
def test_gate_final_plan_runs_nothing_and_detects_g6_by_file(tmp_path):
    out = subprocess.run(
        [_bash(), "scripts/gate_final.sh", "--plan", "--quick", "--skip", "e2e"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    for name in ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "redteam-video", "redteam-audiochat", "e2e"):
        assert f"[{name}]" in out.stdout
    assert "gate_phase4.sh --heldout" in out.stdout and "gate_phase1.sh --quick" in out.stdout
    g6 = next(line for line in out.stdout.splitlines() if "[G6]" in line)
    if (ROOT / "scripts" / "gate_phase6.sh").exists():
        assert "bash scripts/gate_phase6.sh" in g6
    else:
        assert "NOT RUN" in g6
    assert "[e2e] NOT RUN (skipped by --skip)" in out.stdout
    bad = subprocess.run(
        [_bash(), "scripts/gate_final.sh", "--skip", "nope"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert bad.returncode == 2


def _stub_repo(tmp: Path, rcs: dict[str, int], with_g6: bool = False) -> Path:
    (tmp / "scripts").mkdir()
    for f in ("gate_final.sh", "gate_lib.sh"):
        shutil.copy(ROOT / "scripts" / f, tmp / "scripts" / f)
    names = {
        "gate_phase0.sh": "G0", "gate_phase1.sh": "G1", "gate_phase2.sh": "G2", "gate_phase3.sh": "G3",
        "gate_phase4.sh": "G4", "gate_phase5.sh": "G5", "gate_redteam_video.sh": "RV",
        "gate_redteam_audiochat.sh": "RA", "e2e_smoke.sh": "E2E",
    }  # fmt: skip
    if with_g6:
        names["gate_phase6.sh"] = "G6"
    for f, key in names.items():
        (tmp / "scripts" / f).write_text(f"#!/usr/bin/env bash\nexit {rcs.get(key, 0)}\n")
    return tmp


def _run_final(tmp: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [_bash(), "scripts/gate_final.sh", "--report-dir", str(tmp / "rep"), *args],
        cwd=tmp, capture_output=True, text=True, timeout=180, env=env,
    )  # fmt: skip


@needs_bash
@pytest.mark.parametrize(
    ("rcs", "with_g6", "skip", "expected"),
    [
        (
            {},
            True,
            [],
            3,
        ),  # every gate script exits 0 but no gate wrote a report: NOT RUN items remain, never 0
        ({}, False, [], 3),  # gate_phase6.sh missing: NOT RUN
        ({"G1": 3}, True, [], 3),
        ({"G3": 1}, True, [], 1),
        ({"G2": 2, "G3": 1}, True, [], 2),
        ({}, True, ["g1"], 3),  # a skipped gate is NOT RUN
    ],
)
def test_gate_final_exit_codes_with_stub_gates(tmp_path, rcs, with_g6, skip, expected):
    _stub_repo(tmp_path, rcs, with_g6)
    args = ["--skip", ",".join(skip)] if skip else []
    out = _run_final(tmp_path, *args)
    assert out.returncode == expected, out.stdout + out.stderr
    assert "GATE FINAL:" in out.stdout
    assert (tmp_path / "rep" / f"{time.strftime('%Y-%m-%d')}-final.json").exists()
    if not with_g6:
        assert "[G6] NOT RUN (scripts/gate_phase6.sh not found)" in out.stdout
