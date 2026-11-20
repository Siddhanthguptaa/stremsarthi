"""GPU lock exclusion and gate_lib.sh exit-code mapping (spec 9 test 9)."""

import subprocess
import threading
from pathlib import Path

import pytest

from eval.harness.gpu_lock import GpuLockTimeout, gpu_lock, run_locked

ROOT = Path(__file__).resolve().parents[2]


def test_gpu_lock_excludes_and_reports_wait(tmp_path):
    path = str(tmp_path / "gpu.lock")
    with gpu_lock(path=path) as a:
        assert a.waited_s < 0.5
        with pytest.raises(GpuLockTimeout):
            with gpu_lock(timeout_s=0.3, path=path):
                pass
    with gpu_lock(timeout_s=1.0, path=path):
        pass


def test_gpu_lock_waits_for_release(tmp_path):
    path = str(tmp_path / "gpu.lock")
    ready = threading.Event()

    def hold():
        with gpu_lock(path=path):
            ready.set()
            import time

            time.sleep(0.5)

    t = threading.Thread(target=hold)
    t.start()
    ready.wait(2)
    with gpu_lock(timeout_s=5.0, path=path) as info:
        assert info.waited_s >= 0.3
    t.join()


def test_gpu_lock_is_compatible_with_flock_cli(tmp_path):
    path = str(tmp_path / "gpu.lock")
    holder = subprocess.Popen(["flock", path, "sleep", "1.5"])
    import time

    time.sleep(0.4)
    try:
        with pytest.raises(GpuLockTimeout):
            with gpu_lock(timeout_s=0.3, path=path):
                pass
    finally:
        holder.wait()
    assert run_locked(["true"], timeout_s=2).returncode == 0


def _gate(steps: str, tmp_path: Path) -> int:
    script = f"""set -euo pipefail
cd {ROOT}
export GATE_NO_REPORT=1
source scripts/gate_lib.sh
gate_init t
{steps}
gate_finish
"""
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    return p.returncode


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ("gate_step a true; gate_step b true", 0),
        ("gate_step a true; gate_step b bash -c 'exit 1'", 1),
        ("gate_step a true; gate_step b bash -c 'exit 2'", 2),
        ("gate_step a true; gate_step b bash -c 'exit 3'", 3),
        ("gate_step a bash -c 'exit 1'; gate_step b bash -c 'exit 2'", 2),  # env error outranks a failure
        ("gate_step a bash -c 'exit 3'; gate_step b bash -c 'exit 1'", 1),  # failure outranks a skipped leg
        ("gate_step a no_such_command_xyz", 2),  # missing tool is an environment error
        ("gate_step a bash -c 'exit 7'", 1),  # any other non-zero is a failed criterion
        ("gate_step --ok-rc 1 doctor bash -c 'exit 1'", 0),  # warnings only
        ("gate_step --ok-rc 1 doctor bash -c 'exit 2'", 2),
        ("gate_record spike 1 missing", 1),
    ],
)
def test_gate_lib_exit_code_mapping(steps, expected, tmp_path):
    assert _gate(steps, tmp_path) == expected


def test_gate_lib_continues_after_failure_and_prints_table(tmp_path):
    script = f"""cd {ROOT}; export GATE_NO_REPORT=1; source scripts/gate_lib.sh; gate_init t
gate_step first bash -c 'exit 1'; gate_step second true; gate_finish"""
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert p.returncode == 1
    assert "first" in p.stdout and "second" in p.stdout and "FAIL" in p.stdout and "PASS" in p.stdout
    assert "GATE t: FAIL (exit 1)" in p.stdout


def test_gate_report_json_written(tmp_path):
    out = tmp_path / "res.json"
    script = f"""cd {ROOT}; source scripts/gate_lib.sh; gate_init t
gate_step 'quote "x"' true; gate_step bad bash -c 'exit 1'; gate_write_results {out}"""
    subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    import json

    data = json.loads(out.read_text())
    assert [s["mapped_rc"] for s in data["steps"]] == [0, 1] and data["steps"][0]["name"] == 'quote "x"'
