"""The CLI self-test passes, and eval.run behaves on bad input."""

import subprocess
import sys
from pathlib import Path

from eval import run
from eval.selftest import run_selftest

ROOT = Path(__file__).resolve().parents[2]


def test_selftest_all_checks_pass(capsys):
    assert run_selftest(verbose=True)
    out = capsys.readouterr().out
    assert "FAIL" not in out and "selftest: 9/9 passed" in out


def test_cli_selftest_flag_exit_code():
    p = subprocess.run(
        [sys.executable, "-m", "eval.run", "--selftest"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert p.returncode == 0, p.stdout + p.stderr


def test_cli_without_command_is_env_error(capsys):
    assert run.main([]) == run.EXIT_ENV


def test_cli_gate_report_writes_files(tmp_path):
    import json

    res = tmp_path / "r.json"
    res.write_text(
        json.dumps(
            {
                "phase": "p",
                "steps": [
                    {"name": "a", "command": "true", "rc": 0, "mapped_rc": 0, "seconds": 0.1, "note": ""},
                    {"name": "b", "command": "false", "rc": 1, "mapped_rc": 1, "seconds": 0.2, "note": ""},
                ],
            }
        )
    )
    assert (
        run.main(
            [
                "gate-report",
                "--phase",
                "phaseT",
                "--results",
                str(res),
                "--date",
                "2026-01-02",
                "--report-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    data = json.loads((tmp_path / "2026-01-02-phaseT.json").read_text())
    assert data["passed"] is False and len(data["checks"]) == 2


def test_cli_gen_specimen(tmp_path):
    assert run.main(["gen-specimen", "--seed", "5", "--n", "1", "--out", str(tmp_path / "d")]) == 0
    assert (tmp_path / "d" / "manifest.json").exists()
