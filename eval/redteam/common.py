"""Shared bits of the red-team suites: criteria with ceilings, report writing, exit codes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..harness import metrics, report

ROOT = Path(__file__).resolve().parents[2]
EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2


def rate(k: int, n: int) -> dict[str, Any]:
    r = metrics.ratio(k, n)
    return {"k": k, "n": n, "rate": r.value, "ci": [round(r.ci_lo, 4), round(r.ci_hi, 4)]}


def fmt_rate(d: dict[str, Any]) -> str:
    if not d["n"]:
        return "n/a (0)"
    return f"{d['rate']:.1%} ({d['k']}/{d['n']}, CI {d['ci'][0]:.0%}-{d['ci'][1]:.0%})"


def new_report(phase: str, header: dict[str, Any], date: str | None = None) -> report.Report:
    rep = report.Report(phase, date or time.strftime("%Y-%m-%d"))
    rep.header = {"git_rev": report.git_rev(), "profile": "dev", "hardware": report.hardware_info()} | header
    return rep


def finish(rep: report.Report, report_dir: Path | None, record_only: bool) -> int:
    """Write the report. Exit 1 when a hard invariant failed, or a miss-rate ceiling failed and not ``record_only``."""
    md, _ = report.write_report(rep, report_dir or ROOT / "eval" / "reports")
    print(f"report: {md}")
    failed = [c for c in rep.checks if not c.passed and not c.not_run]
    hard = [c for c in failed if not c.name.startswith("miss rate")]
    soft = [c for c in failed if c.name.startswith("miss rate")]
    for c in failed:
        print(f"FAIL {c.name}: {c.value} {c.op} {c.target}", flush=True)
    if hard or (soft and not record_only):
        return EXIT_FAIL
    return EXIT_OK
