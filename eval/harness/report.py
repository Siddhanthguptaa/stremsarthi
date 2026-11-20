"""Report writer: ``eval/reports/<date>-<phase>.md`` plus a stable JSON twin (docs/specs/eval.md 5.6).

Gates read the JSON. Same inputs give byte-identical output (sorted keys, floats rounded to 6 places).
Failing cases are listed by id only; images are never embedded (they regenerate from the seed).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metrics import check

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Check:
    name: str
    value: float | None
    op: str
    target: float
    unit: str = ""
    note: str = ""

    @property
    def passed(self) -> bool:
        return check(self.value, self.op, self.target)


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list[Any]]


@dataclass
class Report:
    phase: str
    date: str  # YYYY-MM-DD
    header: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    failing_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    baseline: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks) and not self.failing_ids


def git_rev(root: Path = ROOT) -> str:
    """Current commit read straight from ``.git`` (no git process). 'unknown' when unavailable."""
    try:
        head = (root / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref:"):
            ref = head.split(None, 1)[1]
            p = root / ".git" / ref
            if p.exists():
                return p.read_text().strip()[:12]
            packed = root / ".git" / "packed-refs"
            if packed.exists():
                for line in packed.read_text().splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0][:12]
            return "unknown"
        return head[:12]
    except OSError:
        return "unknown"


def hardware_info() -> dict[str, str]:
    """CPU and GPU names for the report header (best effort; never raises)."""
    info: dict[str, str] = {"cpu": "unknown", "gpu": "none"}
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            info["gpu"] = out.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return info


def _round(obj: Any) -> Any:
    if isinstance(obj, float):
        return round(obj, 6)
    if isinstance(obj, dict):
        return {str(k): _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def to_json_obj(r: Report) -> dict[str, Any]:
    return _round(
        {
            "phase": r.phase,
            "date": r.date,
            "passed": r.passed,
            "header": r.header,
            "checks": [
                {
                    "name": c.name,
                    "value": c.value,
                    "op": c.op,
                    "target": c.target,
                    "unit": c.unit,
                    "passed": c.passed,
                    "note": c.note,
                }
                for c in r.checks
            ],
            "tables": [{"title": t.title, "columns": t.columns, "rows": t.rows} for t in r.tables],
            "failing_ids": list(r.failing_ids),
            "notes": list(r.notes),
            "baseline": r.baseline,
        }
    )


def _cell(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v).replace("|", "\\|")


def _header_value(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return _cell(v)


def to_markdown(r: Report) -> str:
    lines = [f"# {r.phase} report ({r.date})", "", f"Result: **{'PASS' if r.passed else 'FAIL'}**", ""]
    lines += ["## Run", ""]
    for k in sorted(r.header):
        lines.append(f"- {k}: {_header_value(r.header[k])}")
    if r.checks:
        lines += ["", "## Checks", "", "| check | value | target | result |", "|---|---|---|---|"]
        for c in r.checks:
            unit = f" {c.unit}" if c.unit else ""
            note = f" ({c.note})" if c.note else ""
            lines.append(
                f"| {_cell(c.name)} | {_cell(c.value)}{unit} | {c.op} {_cell(c.target)}{unit} | "
                f"{'PASS' if c.passed else 'FAIL'}{note} |"
            )
    for t in r.tables:
        lines += ["", f"## {t.title}", "", "| " + " | ".join(t.columns) + " |", "|" + "---|" * len(t.columns)]
        for row in t.rows:
            lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    if r.failing_ids:
        lines += ["", "## Failing case ids", ""] + [f"- {i}" for i in r.failing_ids]
    if r.baseline is not None:
        lines += [
            "",
            "## Regression vs baseline",
            "",
            "```json",
            json.dumps(r.baseline, indent=2, sort_keys=True),
            "```",
        ]
    if r.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in r.notes]
    return "\n".join(lines) + "\n"


_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def write_report(r: Report, report_dir: Path) -> tuple[Path, Path]:
    """Write ``<date>-<phase>.md`` and ``.json`` into `report_dir`. Returns (md_path, json_path)."""
    if not _SAFE.match(r.phase) or not _SAFE.match(r.date):
        raise ValueError("phase and date must be filename-safe")
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{r.date}-{r.phase}"
    md = report_dir / f"{stem}.md"
    js = report_dir / f"{stem}.json"
    md.write_text(to_markdown(r), encoding="utf-8")
    js.write_text(json.dumps(to_json_obj(r), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js


def load_report_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
