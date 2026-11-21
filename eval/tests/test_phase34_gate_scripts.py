"""scripts/gate_phase3.sh and gate_phase4.sh: arguments, plan, exit codes (0 pass, 1 fail, 2 environment)."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(script, *args, timeout=60):
    return subprocess.run(
        ["bash", f"scripts/{script}", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout
    )


@pytest.mark.parametrize("script", ["gate_phase3.sh", "gate_phase4.sh"])
def test_help_documents_exit_codes_and_options(script):
    p = run(script, "--help")
    assert p.returncode == 0
    assert (
        "Exit: 0 pass, 1 a criterion failed, 2 environment" in p.stdout
        and "--reference" in p.stdout
        and "--plan" in p.stdout
    )


@pytest.mark.parametrize(
    ("script", "args"),
    [
        ("gate_phase3.sh", ["--nope"]),
        ("gate_phase3.sh", ["--api-stride", "0"]),
        ("gate_phase3.sh", ["--defect", "late_spans"]),
        ("gate_phase3.sh", ["--reference", "--defect", "x"]),
        ("gate_phase4.sh", ["--nope"]),
        ("gate_phase4.sh", ["--defect", "no_scam"]),
        ("gate_phase4.sh", ["--out"]),
    ],
)
def test_bad_arguments_are_exit_2(script, args):
    p = run(script, *args)
    assert p.returncode == 2 and "--help" in p.stderr and "PLAN" not in p.stdout


def test_plans_list_every_leg_and_run_nothing():
    p3 = run("gate_phase3.sh", "--plan")
    assert p3.returncode == 0
    for w in ("[data check]", "[main (APIs up)]", "[API down: fail-safe]", "[API down: local recogniser]"):
        assert w in p3.stdout
    p4 = run("gate_phase4.sh", "--plan")
    assert p4.returncode == 0
    for w in ("[data check]", "[classify (no LLM)]", "[YouTube mock]", "[Twitch mock]"):
        assert w in p4.stdout
    assert "--defect no_scam" in run("gate_phase4.sh", "--plan", "--reference", "--defect", "no_scam").stdout


def test_missing_dataset_that_cannot_be_built_is_exit_2(tmp_path):
    bad = tmp_path / "file"
    bad.write_text("x")
    p = run(
        "gate_phase3.sh",
        "--data",
        str(bad / "sub"),
        "--out",
        str(tmp_path / "o"),
        "--report-dir",
        str(tmp_path / "r"),
        "--allow-tone",
        "--quick",
    )
    assert p.returncode == 2, p.stdout[-400:]
    p = run(
        "gate_phase4.sh",
        "--data",
        str(bad / "sub"),
        "--out",
        str(tmp_path / "o2"),
        "--report-dir",
        str(tmp_path / "r2"),
    )
    assert p.returncode == 2, p.stdout[-400:]


@pytest.mark.slow
def test_reference_gate3_passes_and_defects_fail(tmp_path):
    common = ["--allow-tone", "--data", str(tmp_path / "d"), "--report-dir", str(tmp_path / "r")]
    ok = run("gate_phase3.sh", "--reference", "--out", str(tmp_path / "o1"), *common, timeout=600)
    assert ok.returncode == 0, ok.stdout[-1500:]
    for defect in ("late_spans", "no_failsafe", "no_mouth"):
        bad = run(
            "gate_phase3.sh",
            "--reference",
            "--defect",
            defect,
            "--out",
            str(tmp_path / defect),
            *common,
            timeout=600,
        )
        assert bad.returncode == 1, (defect, bad.stdout[-800:])
    assert (tmp_path / "r").glob("*phase3-reference.md")


@pytest.mark.slow
def test_reference_gate4_passes_and_a_defect_fails(tmp_path):
    common = ["--data", str(tmp_path / "d"), "--report-dir", str(tmp_path / "r"), "--no-llm-leg"]
    ok = run("gate_phase4.sh", "--reference", "--out", str(tmp_path / "o1"), *common, timeout=600)
    assert ok.returncode == 0, ok.stdout[-1500:]
    bad = run(
        "gate_phase4.sh",
        "--reference",
        "--defect",
        "no_scam",
        "--out",
        str(tmp_path / "o2"),
        *common,
        timeout=600,
    )
    assert bad.returncode == 1
