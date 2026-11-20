"""Build ``eval/reports/<date>-phase1.md`` (+ JSON twin) from the leg JSONs a gate run left in one directory.

The gate script (``scripts/gate_phase1.sh``) writes ``<dir>/<leg>.json`` per leg and a step-results file; this
reads them, adds one check per gate step (an environment error or a skipped leg is a failed step, so a missing
leg can never read as a pass), the per-leg checks, latency / perf tables and the regression against
``eval/baselines/phase1.json``. The first passing run writes that baseline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .harness import metrics
from .harness.report import Check, Report, Table, git_rev, hardware_info, write_report

ROOT = Path(__file__).resolve().parents[1]
LEG_ORDER = ("main-cpu", "main-gpu", "failsafe", "hold", "soak", "live")
DELAY_REGRESSION_S = 0.2


def _load(dir_: Path) -> dict[str, dict[str, Any]]:
    legs: dict[str, dict[str, Any]] = {}
    for name in LEG_ORDER:
        p = dir_ / f"{name}.json"
        if p.exists():
            legs[name] = json.loads(p.read_text(encoding="utf-8"))
    return legs


def _row_delay(name: str, m: dict[str, Any]) -> list[Any]:
    t, e = m.get("delay_tap", {}), m.get("delay_egress", {})
    return [
        name,
        t.get("p50_s"),
        t.get("p95_s"),
        t.get("max_s"),
        e.get("p50_s"),
        e.get("p95_s"),
        e.get("max_s"),
    ]


def baseline_values(legs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("main-cpu", "main-gpu"):
        m = legs.get(name, {}).get("measurements")
        if not m:
            continue
        out[name] = {
            "delay_tap_p50_s": m["delay_tap"]["p50_s"],
            "delay_tap_p95_s": m["delay_tap"]["p95_s"],
            "fps": m["fps"]["fps"],
            "cpu_mean_cores": (m.get("cpu") or {}).get("mean_cores"),
        }
    return out


def build_report(
    out_dir: Path,
    gate_results: dict[str, Any] | None = None,
    date: str | None = None,
    baseline_path: Path | None = None,
    extra_header: dict[str, Any] | None = None,
    phase: str = "phase1",
) -> Report:
    legs = _load(out_dir)
    rep = Report(phase, date or time.strftime("%Y-%m-%d"))
    rep.header = {
        "git_rev": git_rev(),
        "profile": "dev",
        "hardware": hardware_info(),
        "seed": 1234,
        "legs_run": sorted(legs),
        "delay_definition": "D-1: capture (optical stamp) to the egress; tap-side and egress_write values reported",
        **(extra_header or {}),
    }
    if gate_results:
        rep.header["commands"] = [s["command"] for s in gate_results.get("steps", [])]
        rows = []
        for s in gate_results.get("steps", []):
            rep.checks.append(
                Check(f"step: {s['name']}", float(s["mapped_rc"]), "==", 0.0, note=s.get("note", ""))
            )
            rows.append([s["name"], s["rc"], s["mapped_rc"], round(s["seconds"], 1)])
        rep.tables.append(Table("Gate steps", ["step", "raw exit", "gate exit", "seconds"], rows))
    for name, leg in legs.items():
        for c in leg.get("checks", []):
            rep.checks.append(
                Check(
                    f"{name}: {c['name']}",
                    c["value"],
                    c["op"],
                    c["target"],
                    c.get("unit", ""),
                    c.get("note", ""),
                )
            )
        if leg.get("skipped"):
            rep.notes.append(f"{name}: skipped ({leg.get('note', '')})")
    mains = [(n, legs[n]["measurements"]) for n in ("main-cpu", "main-gpu") if n in legs]
    if mains:
        rep.tables.append(
            Table(
                "Delay (seconds, capture to tap / to egress socket)",
                ["leg", "tap p50", "tap p95", "tap max", "egress p50", "egress p95", "egress max"],
                [_row_delay(n, m) for n, m in mains],
            )
        )
        rep.tables.append(
            Table(
                "Perf",
                [
                    "leg",
                    "fps",
                    "CPU mean cores",
                    "CPU peak cores",
                    "VRAM peak over baseline MB",
                    "protect fraction",
                    "card frames scored",
                    "card recall",
                ],
                [
                    [
                        n,
                        m["fps"]["fps"],
                        (m.get("cpu") or {}).get("mean_cores"),
                        (m.get("cpu") or {}).get("peak_cores"),
                        (m.get("vram_mb") or {}).get("peak_over_baseline_mb"),
                        m.get("protect_fraction"),
                        m["card"]["visible_frames"],
                        m["card"]["unreadable_recall"],
                    ]
                    for n, m in mains
                ],
            )  # fmt: skip
        )
        stage_rows: dict[str, dict[str, Any]] = {}
        for n, m in mains:
            for stage, st in (m.get("stage_latency_ms") or {}).items():
                stage_rows.setdefault(stage, {})[n] = st
        if stage_rows:
            rep.tables.append(
                Table(
                    "Per-stage latency from the engine timing log (ms, p50 / p95 / max)",
                    ["stage", *[n for n, _ in mains]],
                    [
                        [
                            s,
                            *[
                                (
                                    f"{v[n]['p50_ms']} / {v[n]['p95_ms']} / {v[n]['max_ms']}"
                                    if n in v
                                    else None
                                )
                                for n, _ in mains
                            ],
                        ]
                        for s, v in sorted(stage_rows.items())
                    ],
                )
            )
    if "failsafe" in legs:
        fs = legs["failsafe"]["measurements"].get("failsafe", {})
        rep.notes.append("fail-safe: " + json.dumps(fs, sort_keys=True))
    if "hold" in legs:
        rep.notes.append("hold: " + json.dumps(legs["hold"]["measurements"].get("hold", {}), sort_keys=True))
    if "soak" in legs:
        rep.notes.append(
            "soak memory: " + json.dumps(legs["soak"]["measurements"].get("memory", {}), sort_keys=True)
        )
    if "live" in legs and not legs["live"].get("skipped"):
        rep.notes.append(
            f"live YouTube: viewer-side latency (informational): {legs['live']['measurements'].get('viewer_latency_s')}"
        )
    for n, m in mains:
        if m.get("engine_alignment", {}).get("valid") is False:
            rep.notes.append(
                f"{n}: engine timing log did not align with the tap (egress-socket delay and hold timing need engine_delay and PTS)"
            )
    # regression against the committed baseline (delay p95 not worse by more than 0.2 s)
    bpath = baseline_path or ROOT / "eval" / "baselines" / "phase1.json"
    if bpath.exists():
        base = json.loads(bpath.read_text(encoding="utf-8"))
        reg: dict[str, Any] = {}
        for n, m in mains:
            b = base.get(n)
            if not b:
                continue
            cur = m["delay_tap"]["p95_s"]
            worse = (
                cur is not None
                and b.get("delay_tap_p95_s") is not None
                and metrics.regression(cur, b["delay_tap_p95_s"], DELAY_REGRESSION_S)
            )
            reg[n] = {
                "delay_p95_now": cur,
                "delay_p95_baseline": b.get("delay_tap_p95_s"),
                "regressed": bool(worse),
            }
            rep.checks.append(
                Check(
                    f"{n}: delay p95 vs baseline (worse by <= {DELAY_REGRESSION_S} s)",
                    1.0 if worse else 0.0,
                    "==",
                    0.0,
                )
            )
        rep.baseline = reg or None
    return rep


def run(
    out_dir: Path,
    results: Path | None,
    date: str | None,
    report_dir: Path | None,
    write_baseline: bool = True,
    phase: str = "phase1",
) -> int:
    gate = json.loads(results.read_text(encoding="utf-8")) if results and results.exists() else None
    rep = build_report(out_dir, gate, date, phase=phase)
    md, js = write_report(rep, report_dir or ROOT / "eval" / "reports")
    print(f"report: {md}")
    bpath = ROOT / "eval" / "baselines" / "phase1.json"
    if rep.passed and write_baseline and not bpath.exists():
        values = baseline_values(_load(out_dir))
        if values:
            bpath.parent.mkdir(parents=True, exist_ok=True)
            bpath.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"baseline written: {bpath}")
    return 0 if rep.passed else 1
