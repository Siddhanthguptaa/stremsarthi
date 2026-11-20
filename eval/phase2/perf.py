"""Gate 2, real-time leg: the whole chain (stamped sender, engine, tap) with EVERY detector on.

The sender plays the ``many_targets`` scene (cards, QR, more cards) for 120 s while the real engine
(``python -m engine.main --profile dev``) runs the vision host with all registered detectors. Checks (docs/PLAN.md
section 5 G2, spec 5.9): output fps >= 29, delay p95 <= 5.0 s and max <= 5.5 s (capture to tap, and to the egress
socket when the relay logs it), process-tree CPU <= 5 cores, VRAM growth <= the dev cap on the GPU leg, a card still
unreadable in >= 98 % of its visible frames, and the delay not worse than the Phase 1 baseline by more than 0.2 s.

"All detectors on" is verified from the engine's own ``vision started: ... detectors=[...]`` log line: a detector
the registry knows but the engine did not start is a failed check, not a silent omission.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..harness import metrics
from ..harness.report import Check
from ..phase1 import (
    CARD_RECALL_MIN,
    EXIT_ENV,
    EXIT_HW,
    STAMP_UNREADABLE_MAX,
    _save_timing,
    _write_leg,
    common_measurements,
    preflight_problems,
)
from ..stack import ChainResult, ChainSpec, EnvError, gpu_present, run_chain
from .detectors import discover_each
from .rows import (
    EXPECTED_DETECTORS,
    GATE_CPU_CORES_MAX,
    GATE_DELAY_MAX_S,
    GATE_DELAY_P95_S,
    GATE_DELAY_REGRESSION_S,
    GATE_FPS_MIN,
    GATE_VRAM_CAP_MB,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = "many_targets"
SECONDS = 120.0
PROTECT_FRACTION_MAX = 0.10
_STARTED_RE = re.compile(r"vision started:.*?detectors=\[([^\]]*)\]")


def chk(
    name: str,
    value: float | None,
    op: str,
    target: float,
    unit: str = "",
    note: str = "",
    not_run: bool = False,
) -> Check:
    return Check(name, value, op, target, unit, note, not_run)


def detectors_started(engine_log: str) -> list[str] | None:
    """Detector ids named by the last ``vision started`` log line, None when the line is absent."""
    found = _STARTED_RE.findall(engine_log)
    if not found:
        return None
    return sorted(x.strip().strip("'\"") for x in found[-1].split(",") if x.strip())


def expected_detectors() -> list[str]:
    classes, bad = discover_each()
    return sorted(set(EXPECTED_DETECTORS) | set(classes) | set(bad))


def baseline_delay(label: str, baseline_path: Path | None = None) -> float | None:
    p = baseline_path or ROOT / "eval" / "baselines" / "phase1.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(f"main-{label}", {}).get("delay_tap_p95_s")
    except (OSError, ValueError):
        return None


def analyse_perf(
    res: ChainResult,
    label: str,
    started: list[str] | None,
    expected: list[str],
    baseline_p95: float | None,
    real_engine: bool = True,
) -> tuple[list[Check], dict[str, Any]]:
    m = common_measurements(res)
    d, eg, card = m["delay_tap"], m["delay_egress"], m["card"]
    cpu = (m.get("cpu") or {}).get("mean_cores")
    checks: list[Check] = [
        chk(
            "engine alive at end of run",
            1.0 if res.engine_exit is None else 0.0,
            "==",
            1.0,
            note=f"exit={res.engine_exit}",
        )
    ]
    if not real_engine:
        checks.append(
            chk(
                "all detectors on",
                None,
                "==",
                1.0,
                note="NOT RUN: stand-in engine has no vision host",
                not_run=True,
            )
        )
    elif started is None:
        checks.append(
            chk(
                "all detectors on",
                None,
                "==",
                1.0,
                note="NOT RUN: no 'vision started' line in engine.log",
                not_run=True,
            )
        )
    else:
        absent = [i for i in expected if i not in started]
        checks.append(
            chk(
                "all detectors on",
                float(len(expected) - len(absent)),
                "==",
                float(len(expected)),
                "detectors",
                ("not started: " + ", ".join(absent)) if absent else "started: " + ", ".join(started),
            )
        )
    checks += [
        chk(f"[{label}] output fps (many_targets)", m["fps"]["fps"], ">=", GATE_FPS_MIN, "fps"),
        chk(f"[{label}] delay p95 at tap", d["p95_s"], "<=", GATE_DELAY_P95_S, "s", f"n={d['n']}"),
        chk(f"[{label}] delay max at tap", d["max_s"], "<=", GATE_DELAY_MAX_S, "s"),
    ]
    if eg["n"]:
        checks += [
            chk(f"[{label}] delay p95 at egress socket (D-1)", eg["p95_s"], "<=", GATE_DELAY_P95_S, "s", f"n={eg['n']}"),
            chk(f"[{label}] delay max at egress socket", eg["max_s"], "<=", GATE_DELAY_MAX_S, "s"),
        ]  # fmt: skip
    if baseline_p95 is None:
        checks.append(
            chk(
                f"[{label}] delay p95 vs the Phase 1 baseline (worse by <= {GATE_DELAY_REGRESSION_S} s)",
                None,
                "<=",
                GATE_DELAY_REGRESSION_S,
                "s",
                "NOT RUN: eval/baselines/phase1.json has no baseline (the Phase 1 gate has not passed yet)",
                not_run=True,
            )
        )
    else:
        worse = None if d["p95_s"] is None else round(d["p95_s"] - baseline_p95, 4)
        checks.append(
            chk(
                f"[{label}] delay p95 vs the Phase 1 baseline (worse by <= {GATE_DELAY_REGRESSION_S} s)",
                worse,
                "<=",
                GATE_DELAY_REGRESSION_S,
                "s",
                f"now {d['p95_s']}, baseline {baseline_p95}; "
                f"{'REGRESSED' if d['p95_s'] is not None and metrics.regression(d['p95_s'], baseline_p95, GATE_DELAY_REGRESSION_S) else 'ok'}",
            )
        )
    checks += [
        chk(f"[{label}] CPU, process tree mean", cpu, "<=", GATE_CPU_CORES_MAX, "cores", f"peak {(m.get('cpu') or {}).get('peak_cores')}"),
        chk(f"[{label}] card unreadable, frame recall", card["unreadable_recall"], ">=", CARD_RECALL_MIN, note=f"{card['visible_frames']} card-visible frames; CI {card.get('recall_ci')}"),
        chk(f"[{label}] Verhoeff-valid OCR reads of the card", float(card["ocr_valid_reads"]), "==", 0.0, "reads"),
        chk(f"[{label}] stamp unreadable (not slates)", m["stamp"]["stamp_unreadable_frac"], "<=", STAMP_UNREADABLE_MAX),
        chk(f"[{label}] protect (slate) fraction while live", m["protect_fraction"], "<=", PROTECT_FRACTION_MAX),
    ]  # fmt: skip
    if res.spec.gpu:
        v = (res.vram_mb or {}).get("peak_over_baseline_mb")
        checks.append(
            chk(
                f"[{label}] VRAM growth over the idle baseline",
                v,
                "<=",
                GATE_VRAM_CAP_MB,
                "MB",
                f"peak {(res.vram_mb or {}).get('peak_mb')} MB, idle {(res.vram_mb or {}).get('baseline_mb')} MB; dev cap 3.5 GB"
                if v is not None
                else "NOT RUN: nvidia-smi gave no samples",
                not_run=v is None,
            )
        )
    m["detectors_expected"] = expected
    m["detectors_started"] = started
    return checks, m


def _print(leg: str, checks: list[Check]) -> None:
    print(f"leg {leg}:")
    for c in checks:
        v = "n/a" if c.value is None else f"{c.value:.4g}"
        print(
            f"  [{c.status}] {c.name}: {v} {c.op} {c.target:g} {c.unit} {('(' + c.note + ')') if c.note else ''}"
        )


def run_perf(out: Path, engine: str, gpu: bool, seconds: float = SECONDS) -> int:
    label = "gpu" if gpu else "cpu"
    leg = f"perf-{label}"
    if gpu and not gpu_present():
        print("no GPU found: the GPU leg cannot run")
        return EXIT_HW
    try:
        res = run_chain(ChainSpec(out / leg, SCRIPT, seconds, engine=engine, gpu=gpu))
    except EnvError as exc:
        print(f"ENV ERROR: {exc}")
        return EXIT_ENV
    _save_timing(res, out, leg)
    log_path = out / leg / "engine.log"
    text = log_path.read_text(errors="ignore") if log_path.exists() else ""
    checks, m = analyse_perf(
        res,
        label,
        detectors_started(text),
        expected_detectors(),
        baseline_delay(label),
        real_engine=engine == "real",
    )
    _print(leg, checks)
    return _write_leg(out, leg, checks, m, res)


def preflight(engine: str) -> list[str]:
    return preflight_problems(engine)


__all__ = [
    "analyse_perf",
    "baseline_delay",
    "detectors_started",
    "expected_detectors",
    "preflight",
    "run_perf",
]
