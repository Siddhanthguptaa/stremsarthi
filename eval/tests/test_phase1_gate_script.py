"""scripts/gate_phase1.sh: argument handling, the printed plan, exit codes, secrets (T1.4).

Nothing here starts a stream: ``--plan`` prints the steps and exits, and the report hook is tested with fake steps.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "scripts/gate_phase1.sh"
SECRET = "zzzz-test-key-do-not-print-1234"


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    e = {k: v for k, v in os.environ.items() if k != "YOUTUBE_STREAM_KEY"}
    e.update(env or {})
    return subprocess.run(
        ["bash", SCRIPT, *args], cwd=ROOT, capture_output=True, text=True, env=e, timeout=60
    )


def steps(p: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in p.stdout.splitlines() if line.startswith("PLAN: [")]


def test_help_works_and_documents_every_option():
    for flag in ("--help", "-h"):
        p = run(flag)
        assert p.returncode == 0, p.stderr
        for word in (
            "--live-youtube",
            "--cpu-only",
            "--minutes",
            "--require-gpu",
            "--engine",
            "YOUTUBE_STREAM_KEY",
            "flock /tmp/streamsarthi-gpu.lock",
            "5.0 s",
        ):
            assert word in p.stdout, word
        assert "Exit: 0 pass, 1 a criterion failed, 2 environment" in p.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--nope"],
        ["extra"],
        ["--minutes"],
        ["--minutes", "abc"],
        ["--minutes", "-3"],
        ["--minutes", "1.2.3"],
        ["--engine", "fake"],
        ["--engine"],
        ["--viewer-latency-s", "slow"],
        ["--out"],
        ["--cpu-only", "--require-gpu"],
    ],
)
def test_bad_arguments_are_an_environment_error_with_a_hint(args):
    p = run(*args)
    assert p.returncode == 2, (args, p.stdout, p.stderr)
    assert "gate_phase1.sh:" in p.stderr and "--help" in p.stderr
    assert "PLAN" not in p.stdout  # nothing was planned, let alone run


def test_default_plan_runs_cpu_and_gpu_main_failsafe_and_hold():
    p = run("--plan")
    assert p.returncode == 0, p.stderr
    s = steps(p)
    names = [x.split("]")[0] for x in s]
    assert names == [
        "PLAN: [preflight",
        "PLAN: [main leg (cpu)",
        "PLAN: [main leg (gpu)",
        "PLAN: [fail-safe (lane hang)",
        "PLAN: [hold (bus, retro, fence)",
        "PLAN: [soak",
    ]
    gpu = next(x for x in s if "main leg (gpu)" in x)
    assert "flock -E 2 -w 1800 /tmp/streamsarthi-gpu.lock" in gpu  # every GPU step is under the lock
    assert "SS_GPU_LOCK_HELD=1" in gpu and "--gpu" in gpu
    assert "(exit 3 accepted)" in gpu  # no GPU is a skipped leg unless --require-gpu
    assert "not run: pass --minutes N" in next(x for x in s if "[soak]" in x)
    assert not any("live YouTube" in x for x in s)
    cpu = next(x for x in s if "main leg (cpu)" in x)
    assert "--leg main" in cpu and "flock" not in cpu
    assert "report -> eval/reports/<date>-phase1.md" in p.stdout


def test_cpu_only_skips_the_gpu_leg():
    s = steps(run("--plan", "--cpu-only"))
    gpu = next(x for x in s if "main leg (gpu)" in x)
    assert "not run: --cpu-only" in gpu and "flock" not in gpu


def test_require_gpu_makes_a_missing_gpu_fatal():
    gpu = next(x for x in steps(run("--plan", "--require-gpu")) if "main leg (gpu)" in x)
    assert "(exit 3 accepted)" not in gpu and "flock" in gpu


def test_minutes_adds_the_soak_step():
    for value in ("2", "0.5", "30"):
        soak = next(x for x in steps(run("--plan", "--minutes", value)) if "soak" in x)
        assert f"--leg soak --minutes {value}" in soak
    zero = next(x for x in steps(run("--plan", "--minutes", "0")) if "soak" in x)
    assert "not run" in zero
    eq = next(x for x in steps(run("--plan", "--minutes=3")) if "soak" in x)
    assert "--leg soak --minutes 3" in eq


def test_live_youtube_without_a_key_skips_that_leg_only():
    p = run("--plan", "--live-youtube")
    assert p.returncode == 0
    assert "SKIP live-youtube: YOUTUBE_STREAM_KEY is not set" in p.stdout
    s = steps(p)
    live = next(x for x in s if "live YouTube" in x)
    assert "(not run)" in live and "SKIPPED" in live
    assert any("main leg (cpu)" in x and "(not run)" not in x for x in s)  # the other legs still run


def test_live_youtube_with_a_key_plans_the_leg_and_never_prints_the_key():
    p = run("--plan", "--live-youtube", "--viewer-latency-s", "7.5", env={"YOUTUBE_STREAM_KEY": SECRET})
    assert p.returncode == 0
    assert SECRET not in p.stdout and SECRET not in p.stderr
    live = next(x for x in steps(p) if "live YouTube" in x)
    assert "--leg live" in live and "--viewer-latency-s 7.5" in live and "(not run)" not in live
    assert "SKIP live-youtube" not in p.stdout


def test_engine_choice_changes_the_report_name():
    assert "<date>-phase1.md" in run("--plan").stdout
    assert "<date>-phase1-ref-broken.md" in run("--plan", "--engine", "ref-broken").stdout
    main = next(x for x in steps(run("--plan", "--engine=ref-broken")) if "main leg (cpu)" in x)
    assert "--engine ref-broken" in main


def test_quick_mode_shortens_the_main_leg_and_never_writes_a_baseline():
    p = run("--plan", "--quick")
    assert p.returncode == 0
    s = steps(p)
    assert "--leg main --seconds 45" in next(x for x in s if "main leg (cpu)" in x)
    assert "--leg main --gpu --seconds 45" in next(x for x in s if "main leg (gpu)" in x)
    assert "<date>-phase1-quick.md" in p.stdout
    assert "--seconds" not in next(x for x in steps(run("--plan")) if "main leg (cpu)" in x)
    assert "--quick" in run("--help").stdout


def test_script_never_echoes_the_environment_or_enables_xtrace():
    text = (ROOT / SCRIPT).read_text(encoding="utf-8")
    assert "set -x" not in text and "printenv" not in text and "env |" not in text
    for line in text.splitlines():
        # the key is only ever tested for emptiness, never expanded where it could be printed or passed on
        if "YOUTUBE_STREAM_KEY" in line and "${YOUTUBE_STREAM_KEY" in line:
            assert '[ -z "${YOUTUBE_STREAM_KEY:-}" ]' in line, line


def test_every_gpu_step_is_wrapped_in_the_lock():
    text = (ROOT / SCRIPT).read_text(encoding="utf-8")
    gpu_lines = [ln for ln in text.splitlines() if "--leg main --gpu" in ln]
    assert gpu_lines and all("flock" in ln for ln in gpu_lines)


# ---------------------------------------------------------------- exit codes with fake legs


@pytest.fixture
def fakepy(tmp_path):
    """A `python` shim first on PATH: `eval.run phase1 --leg X` exits FAKE_RC_X (FAKE_RC_X_GPU for --gpu),
    everything else (the report builder) runs the real interpreter. Records every leg call."""
    shim = tmp_path / "shim"
    shim.mkdir()
    real = sys.executable
    script = f"""#!/bin/bash
args="$*"
case "$args" in
  *"eval.run phase1 "*"--leg "*)
    leg=$(echo "$args" | sed -E 's/.*--leg ([a-z]+).*/\\1/')
    suffix=""; case "$args" in *"--gpu"*) suffix="_GPU" ;; esac
    var="FAKE_RC_$(echo "$leg" | tr a-z A-Z)$suffix"
    echo "$leg$suffix $args" >> "$FAKE_LOG"
    exit "${{!var:-0}}" ;;
  *) exec {real} "$@" ;;
esac
"""
    (shim / "python").write_text(script, encoding="utf-8")
    (shim / "python").chmod(0o755)
    return shim


def gate(fakepy, tmp_path, *args, env=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "legs.log"
    e = {
        "PATH": f"{fakepy}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "SS_GPU_LOCK_FILE": str(tmp_path / "gpu.lock"),
        "GATE_RESULTS_FILE": str(tmp_path / "results.json"),
    }
    e.update(env or {})
    p = run(
        "--engine",
        "ref",
        "--out",
        str(tmp_path / "out"),
        "--report-dir",
        str(tmp_path / "reports"),
        *args,
        env=e,
    )
    legs = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return p, legs


def test_all_legs_pass_is_exit_0_and_writes_the_report(fakepy, tmp_path):
    p, legs = gate(fakepy, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert [x.split()[0] for x in legs] == ["preflight", "main", "main_GPU", "failsafe", "hold"]
    assert "GATE phase1-ref: PASS (exit 0)" in p.stdout
    reports = list((tmp_path / "reports").glob("*-phase1-ref.md"))
    assert len(reports) == 1 and (tmp_path / "reports" / reports[0].name.replace(".md", ".json")).exists()
    assert "main leg (cpu)" in reports[0].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("fake", "expected"),
    [
        ({"FAKE_RC_MAIN": "1"}, 1),
        ({"FAKE_RC_FAILSAFE": "1"}, 1),
        ({"FAKE_RC_HOLD": "1"}, 1),
        ({"FAKE_RC_HOLD": "2"}, 2),  # hooks missing or environment error
        ({"FAKE_RC_MAIN": "1", "FAKE_RC_HOLD": "2"}, 2),  # environment outranks a failure
        ({"FAKE_RC_MAIN": "139"}, 1),  # a crashed leg is a failed criterion, not a pass
        ({"FAKE_RC_MAIN_GPU": "1"}, 1),
    ],
)
def test_a_failing_leg_fails_the_gate_but_the_other_legs_still_run(fakepy, tmp_path, fake, expected):
    p, legs = gate(fakepy, tmp_path, env=fake)
    assert p.returncode == expected, p.stdout + p.stderr
    assert [x.split()[0] for x in legs] == ["preflight", "main", "main_GPU", "failsafe", "hold"]
    assert "FAIL" in p.stdout or "ENV" in p.stdout


def test_a_failed_preflight_stops_before_any_leg_runs(fakepy, tmp_path):
    p, legs = gate(fakepy, tmp_path, env={"FAKE_RC_PREFLIGHT": "2"})
    assert p.returncode == 2, p.stdout + p.stderr
    assert [x.split()[0] for x in legs] == ["preflight"]
    assert "no leg is run" in p.stdout and "ENVIRONMENT ERROR" in p.stdout


def test_missing_gpu_is_a_skip_unless_required(fakepy, tmp_path):
    p, _ = gate(fakepy, tmp_path / "a", env={"FAKE_RC_MAIN_GPU": "3"})
    assert p.returncode == 0, p.stdout + p.stderr
    report = next((tmp_path / "a" / "reports").glob("*-phase1-ref.md")).read_text(encoding="utf-8")
    assert "exit 3 accepted" in report  # the skipped GPU leg is visible in the report, not silent
    p, _ = gate(fakepy, tmp_path / "b", "--require-gpu", env={"FAKE_RC_MAIN_GPU": "3"})
    assert p.returncode == 3, p.stdout + p.stderr
    assert "REQUIRED HARDWARE LEG SKIPPED" in p.stdout


def test_cpu_only_never_runs_the_gpu_leg(fakepy, tmp_path):
    p, legs = gate(fakepy, tmp_path, "--cpu-only", env={"FAKE_RC_MAIN_GPU": "1"})
    assert p.returncode == 0
    assert all("_GPU" not in x.split()[0] for x in legs)


def test_soak_runs_only_with_minutes(fakepy, tmp_path):
    p, legs = gate(fakepy, tmp_path, "--minutes", "2", env={"FAKE_RC_SOAK": "1"})
    assert p.returncode == 1
    soak = [x for x in legs if x.startswith("soak ")]
    assert len(soak) == 1 and "--minutes 2" in soak[0]


def test_live_youtube_without_a_key_skips_only_that_leg(fakepy, tmp_path):
    p, legs = gate(fakepy, tmp_path, "--live-youtube", env={"FAKE_RC_LIVE": "1"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "SKIP live-youtube" in p.stdout
    assert not any(x.startswith("live") for x in legs)
    assert [x.split()[0] for x in legs] == ["preflight", "main", "main_GPU", "failsafe", "hold"]


def test_live_youtube_with_a_key_runs_the_leg_and_never_prints_the_key(fakepy, tmp_path):
    p, legs = gate(
        fakepy, tmp_path, "--live-youtube", "--viewer-latency-s", "6.5", env={"YOUTUBE_STREAM_KEY": SECRET}
    )
    assert p.returncode == 0, p.stdout + p.stderr
    live = [x for x in legs if x.startswith("live ")]
    assert len(live) == 1 and "--viewer-latency-s 6.5" in live[0]
    assert SECRET not in p.stdout and SECRET not in p.stderr and SECRET not in " ".join(legs)
    assert SECRET not in (tmp_path / "results.json").read_text(encoding="utf-8")
    failing, _ = gate(
        fakepy, tmp_path / "f", "--live-youtube", env={"YOUTUBE_STREAM_KEY": SECRET, "FAKE_RC_LIVE": "1"}
    )
    assert failing.returncode == 1 and SECRET not in failing.stdout + failing.stderr


# ---------------------------------------------------------------- gate_lib: report hook and combined codes


def _lib(snippet: str) -> subprocess.CompletedProcess[str]:
    script = f"""cd {ROOT}; source scripts/gate_lib.sh; gate_init t
export GATE_RESULTS_FILE=$(mktemp)
{snippet}"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=30)


def test_gate_lib_calls_the_report_hook_with_the_results_file():
    p = _lib("""hook() { echo "HOOK called"; test -s "$1" && echo HAS_JSON; }
GATE_REPORT_HOOK=hook
gate_step ok true; gate_step bad bash -c 'exit 1'
gate_finish""")
    assert p.returncode == 1
    assert "HOOK called" in p.stdout and "HAS_JSON" in p.stdout
    assert "GATE t: FAIL (exit 1)" in p.stdout


def test_a_failing_report_hook_does_not_change_the_gate_result():
    p = _lib("""hook() { return 7; }
GATE_REPORT_HOOK=hook
gate_step ok true
gate_finish""")
    assert p.returncode == 0 and "GATE t: PASS (exit 0)" in p.stdout


@pytest.mark.parametrize(
    ("codes", "expected"),
    [("0 0", 0), ("0 3", 3), ("3 1", 1), ("1 2", 2), ("3 3", 3), ("0", 0)],
)
def test_gate_combine_precedence(codes, expected):
    p = subprocess.run(
        ["bash", "-c", f"source {ROOT}/scripts/gate_lib.sh; gate_combine {codes}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.stdout.strip() == str(expected)
