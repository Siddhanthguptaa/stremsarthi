"""Build ``eval/reports/<date>-phase2.md`` (+ JSON twin) from the leg files a gate run left in one directory.

Reads ``offline.json`` (accuracy), ``perf-cpu.json`` / ``perf-gpu.json`` (real-time) and the gate's step results.
Every gate step is a check (an environment error or a skipped leg is a failed step, never a pass); every leg check is
carried over; a criterion that could not be evaluated is shown as NOT RUN. The first passing full run writes
``eval/baselines/phase2.json`` (numbers only).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..harness.report import Check, Report, Table, git_rev, hardware_info, write_report
from .rows import ALL_ROWS, GATE_LEVELS, LEVELS, ROWS

ROOT = Path(__file__).resolve().parents[2]
LEGS = ("offline", "perf-cpu", "perf-gpu")
SRC_LABEL = {"img": "stills", "clip": "clips"}


def _load(dir_: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in LEGS:
        p = dir_ / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text(encoding="utf-8"))
    return out


def _f(v: Any, nd: int = 3) -> Any:
    return None if v is None else round(float(v), nd)


def _status_of(checks: list[dict[str, Any]], title: str) -> str:
    mine = [c for c in checks if c["name"].startswith(f"{title} recall [")]
    if not mine:
        return "n/a"
    if any(c.get("not_run") for c in mine):
        return "NOT RUN"
    return "PASS" if all(c["passed"] for c in mine) else "FAIL"


def _lv(blk: dict[str, Any]) -> str:
    return "n/a" if not blk["n"] else f"{blk['value']:.3f} (n={blk['n']})"


def offline_tables(leg: dict[str, Any]) -> tuple[list[Table], dict[str, Any], list[str]]:
    m = leg["measurements"]
    checks = leg["checks"]
    rows = m.get("rows", {})
    tables: list[Table] = []
    data: dict[str, Any] = {}
    notes: list[str] = []
    cat_rows, lvl_rows, attr_rows, missed_rows = [], [], [], []
    warm_rows: list[list[Any]] = []
    warm_note = ""
    for r in ALL_ROWS:
        st = rows.get(r.key)
        if st is None:
            continue
        status = _status_of(checks, r.title) if r.gated else ("NOT RUN" if st["status"] != "ok" else "info")
        img, clip = st["img"]["gate"], st["clip"]["gate"]
        warm = st["clip"].get("warmup")
        label = r.title
        if warm:  # the warm-up rule is stated right next to the gate row
            label += f" [clips: settled, >= {warm['settled_s']:g} s after the track first appears]"
        cat_rows.append(
            [
                label,
                r.detector,
                img["n"],
                _f(img["value"]),
                f"[{img['ci_lo']:.3f}, {img['ci_hi']:.3f}]" if img["n"] else "n/a",
                clip["n"],
                _f(clip["value"]),
                r.target,
                status,
            ]
        )
        for src in ("img", "clip"):
            if st[src]["all"]["n"]:
                name = r.title + (" (settled frames)" if src == "clip" and st[src].get("warmup") else "")
                lvl_rows.append([name, SRC_LABEL[src], *[_lv(st[src]["levels"][lv]) for lv in LEVELS]])
        for attr, groups in st["img"]["attrs"].items():
            attr_rows.append([r.title, attr, "; ".join(f"{k}: {_lv(b)}" for k, b in groups.items())])
        for src in ("img", "clip"):
            if st[src]["missed"] and status == "FAIL":
                missed_rows.append(
                    [r.key, SRC_LABEL[src], len(st[src]["missed"]), " ".join(st[src]["missed"][:40])]
                )
        if warm:
            warm_rows.append(
                [
                    f"settled (gate row, >= {warm['settled_s']:g} s)",
                    *[_lv(warm["settled"]["levels"][lv]) for lv in LEVELS],
                    _lv(warm["settled"]["gate"]),
                ]
            )
            warm_rows.append(
                [
                    f"steady (>= {warm['steady_s']:g} s)",
                    *[_lv(warm["steady"]["levels"][lv]) for lv in LEVELS],
                    _lv(warm["steady"]["gate"]),
                ]
            )
            warm_rows.append(
                [
                    "all frames (includes the warm-up)",
                    *[_lv(warm["all"]["levels"][lv]) for lv in LEVELS],
                    _lv(warm["all"]["gate"]),
                ]
            )
            warm_note = warm["rule"]
        data[r.key] = {
            "status": status,
            "reason": st["reason"],
            "target": r.target,
            "stills": {"n": img["n"], "recall": img["value"], "ci": [img["ci_lo"], img["ci_hi"]]},
            "clips": {"n": clip["n"], "recall": clip["value"], "ci": [clip["ci_lo"], clip["ci_hi"]]},
            "levels": {
                SRC_LABEL[s]: {lv: {"n": st[s]["levels"][lv]["n"], "recall": st[s]["levels"][lv]["value"]} for lv in LEVELS}
                for s in ("img", "clip")
            },
        }  # fmt: skip
        if warm:
            data[r.key]["warmup"] = {
                k: {"recall": warm[k]["gate"]["value"], "n": warm[k]["gate"]["n"]}
                for k in ("settled", "steady", "all")
            } | {"settled_s": warm["settled_s"], "steady_s": warm["steady_s"]}
        if status == "NOT RUN" and st["reason"]:
            notes.append(f"NOT RUN: {r.title}: {st['reason']}")
    sc = m.get("scoring", {})
    tables.append(
        Table(
            f"Per-category frame-level recall (levels {'+'.join(sc.get('gate_levels', LEVELS))}; hit = masks cover >= "
            f"{sc.get('cover_min', 0.9):.2f} of the target quad)",
            [
                "category",
                "detector",
                "stills n",
                "stills recall",
                "stills 95% CI",
                "clips n (frames)",
                "clips recall",
                "target",
                "result",
            ],
            cat_rows,
        )  # fmt: skip
    )
    tables.append(
        Table(
            "Recall by difficulty level (all levels reported, whatever the gate uses)",
            ["category", "set", *LEVELS],
            lvl_rows,
        )
    )
    gl = tuple(sc.get("gate_levels", GATE_LEVELS))
    stress_lv = [lv for lv in LEVELS if lv not in gl]
    if stress_lv:
        stress_rows = []
        for r in ROWS:
            st = rows.get(r.key)
            if st is None:
                continue
            for src in ("img", "clip"):
                if not st[src]["all"]["n"]:
                    continue
                for lv in stress_lv:
                    b = st[src]["levels"][lv]
                    if b["n"]:
                        meets = "yes" if b["value"] >= (r.target or 0.0) else "no"
                        name = r.title + (
                            " (settled frames)" if src == "clip" and st[src].get("warmup") else ""
                        )
                        stress_rows.append(
                            [name, SRC_LABEL[src], lv, b["n"], _f(b["value"]), r.target, meets]
                        )
        tables.append(
            Table(
                f"Stress levels ({'+'.join(stress_lv)}): reported, NOT gated (D-48); the gate targets are shown for "
                "reference only",
                ["category", "set", "level", "n", "recall", "target (reference)", "meets target"],
                stress_rows,
            )
        )
    if warm_rows:
        tables.append(
            Table(
                "Allowlisted faces left visible on clips: settled / steady / all frames. " + warm_note,
                ["view", *LEVELS, "gate levels"],
                warm_rows,
            )
        )
        notes.append("warm-up rule (allowlisted faces on clips): " + warm_note)
    if attr_rows:
        tables.append(
            Table("Recall by sub-group (stills)", ["category", "attribute", "recall by value"], attr_rows)
        )
    prec = m.get("precision", {})
    prow = [
        [c, v["masks"], _f(v["precision_inside"]["value"]), _f(v["precision_iou50"]["value"]), v["stray"]]
        for c, v in prec.get("by_category", {}).items()
    ]
    for src, a in (prec.get("all") or {}).items():
        prow.append(
            [
                f"all/{src}",
                a["masks"],
                _f(a["precision_inside"]["value"]),
                _f(a["precision_iou50"]["value"]),
                a["stray"],
            ]
        )
    tables.append(
        Table(
            "Mask precision (informational; 'inside' = >= 50 % of the mask lies on ground truth that justifies its "
            "category, 'IoU 0.5' = spec 5.5 against a single target, 'stray' = masks on frames that hold nothing of "
            "that category; the planner merges and pads nearby boxes, which lowers both precision figures)",
            ["mask category/set", "masks", "precision (inside)", "precision (IoU >= 0.5)", "stray masks"],
            prow,
        )
    )
    neg = m.get("negatives", {})
    nrow = [
        [
            b,
            v["n"],
            v["false"],
            _f(v["rate"]),
            ", ".join(v.get("counted_cats") or []) or "any mask of the detector",
            v.get("info") if v.get("info_cats") else None,
            _f(v.get("info_rate")) if v.get("info_cats") else None,
            v["protect"],
        ]
        for b, v in neg.items()
    ]
    dis = m.get("distractors", {}).get("all", {})
    tables.append(
        Table(
            f"Hard negatives: items/frames where the detector under test produced a false mask (distractors masked: "
            f"{dis.get('k', 0)}/{dis.get('n', 0)}). neg_text is read on PII masks only (phone_number, pin_code, "
            "upi_id, email, pan): its signboard masks come from the text detector's mask_all_text rule, by design. "
            "neg_scene (natural scenes and text-free UI) is the signboard negative: informational, not gated",
            [
                "bucket/set",
                "n",
                "false masks",
                "rate",
                "counted as false",
                "signboard-only masks",
                "signboard rate",
                "slated (protect)",
            ],
            nrow,
        )
    )
    fl = m.get("flicker", {})
    tables.append(
        Table(
            "Flicker on static clips (mask on, off, on inside one second on a continuously visible target)",
            ["scope", "windows", "flickering", "fraction"],
            [["all", fl.get("windows"), fl.get("flickering"), _f(fl.get("fraction"), 4)]]
            + [
                [f"row {k}", v["windows"], v["flickering"], _f(v["fraction"], 4)]
                for k, v in fl.get("by_row", {}).items()
            ]
            + [
                [f"level {k}", v["windows"], v["flickering"], _f(v["fraction"], 4)]
                for k, v in fl.get("by_level", {}).items()
            ],
        )  # fmt: skip
    )
    lat = m.get("latency", {})
    lrow = []
    prod = lat.get("production") or {}
    for k, v in (prod.get("lanes_ms") or {}).items():
        lrow.append(
            [
                f"{k}, production OCR budget {prod.get('budget_ms')} ms (judged)",
                v["n"], v["p50_ms"], v["p95_ms"], v["max_ms"], v.get("budget_ms"), v.get("within_budget"),
            ]
        )  # fmt: skip
    for k, v in lat.get("lanes_ms", {}).items():
        lrow.append(
            [
                f"{k}, recall pass, OCR budget lifted (ceiling, informational)",
                v["n"], v["p50_ms"], v["p95_ms"], v["max_ms"], v.get("budget_ms"), v.get("within_budget"),
            ]
        )  # fmt: skip
    tables.append(
        Table(
            "Latency per lane, offline CPU (ms; budget from docs/PLAN.md 2.5). The 5000 ms OCR budget lift is used for "
            "the recall pass ONLY; the lane is judged on the production-budget pass (latency stills of every bucket; "
            "shared machine, so indicative)",
            ["lane / pass", "runs", "p50", "p95", "max", "budget p95", "within"],
            lrow,
        )
    )
    if prod.get("rows"):
        tables.append(
            Table(
                f"Recall at the production OCR budget ({prod.get('budget_ms')} ms, budget-capped) vs the lifted budget "
                f"({prod.get('lifted_budget_ms')} ms), same {prod.get('images')} stills (informational)",
                ["row", "visible target frames", "production budget", "lifted budget"],
                [
                    [k, v["n"], _f(v["recall_production"]), _f(v["recall_lifted"])]
                    for k, v in prod["rows"].items()
                ],
            )
        )
    drow = [
        [k, v["n"], v["p50_ms"], v["p95_ms"], v["max_ms"]] for k, v in lat.get("detectors_ms", {}).items()
    ]
    tables.append(
        Table("Latency per detector, offline CPU (ms)", ["detector", "calls", "p50", "p95", "max"], drow)
    )
    if missed_rows:
        tables.append(
            Table(
                "Missed case ids of failing rows (ids only; images regenerate from the seed)",
                ["row", "set", "items", "ids"],
                missed_rows,
            )
        )
    det = m.get("detectors", {})
    tables.append(
        Table(
            "Detectors",
            ["id", "state", "lane", "categories", "detail"],
            [
                [i, d["state"], d.get("lane"), ",".join(d.get("categories", [])), d.get("detail")]
                for i, d in det.items()
            ],
        )
    )
    data["flicker"] = {k: fl.get(k) for k in ("windows", "flickering", "fraction", "static_clips")}
    data["detectors"] = {i: d["state"] for i, d in det.items()}
    data["lanes_ms"] = lat.get("lanes_ms", {})
    data["lanes_ms_production"] = prod.get("lanes_ms", {})
    return tables, data, notes


def perf_tables(legs: dict[str, dict[str, Any]]) -> list[Table]:
    mains = [(n, legs[n]["measurements"]) for n in ("perf-cpu", "perf-gpu") if n in legs]
    if not mains:
        return []
    tables = [
        Table(
            "Real-time leg with all detectors on (many_targets, capture to tap / egress)",
            [
                "leg",
                "fps",
                "delay p50",
                "p95",
                "max",
                "egress p95",
                "CPU mean cores",
                "CPU peak",
                "VRAM over idle MB",
                "protect",
                "card recall",
            ],
            [
                [
                    n,
                    m["fps"]["fps"],
                    m["delay_tap"]["p50_s"],
                    m["delay_tap"]["p95_s"],
                    m["delay_tap"]["max_s"],
                    m["delay_egress"]["p95_s"],
                    (m.get("cpu") or {}).get("mean_cores"),
                    (m.get("cpu") or {}).get("peak_cores"),
                    (m.get("vram_mb") or {}).get("peak_over_baseline_mb"),
                    _f(m.get("protect_fraction")),
                    m["card"]["unreadable_recall"],
                ]
                for n, m in mains
            ],
        )  # fmt: skip
    ]
    stages: dict[str, dict[str, Any]] = {}
    for n, m in mains:
        for stage, st in (m.get("stage_latency_ms") or {}).items():
            stages.setdefault(stage, {})[n] = st
    if stages:
        tables.append(
            Table(
                "Per-stage latency from the engine timing log (ms, p50 / p95 / max)",
                ["stage", *[n for n, _ in mains]],
                [
                    [s, *[(f"{v[n]['p50_ms']} / {v[n]['p95_ms']} / {v[n]['max_ms']}" if n in v else None) for n, _ in mains]]
                    for s, v in sorted(stages.items())
                ],
            )
        )  # fmt: skip
    return tables


def baseline_values(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": {k: {"stills": v["stills"]["recall"], "clips": v["clips"]["recall"]} for k, v in data.get("rows", {}).items()},
        "flicker": data.get("flicker", {}).get("fraction"),
        "lane_p95_ms": {k: v.get("p95_ms") for k, v in (data.get("lanes_ms_production") or data.get("lanes_ms", {})).items()},
        "perf": data.get("perf", {}),
    }  # fmt: skip


def build_report(
    out_dir: Path,
    gate_results: dict[str, Any] | None = None,
    date: str | None = None,
    phase: str = "phase2",
    baseline_path: Path | None = None,
    extra_header: dict[str, Any] | None = None,
) -> Report:
    legs = _load(out_dir)
    rep = Report(phase, date or time.strftime("%Y-%m-%d"))
    rep.header = {
        "git_rev": git_rev(),
        "profile": "dev",
        "hardware": hardware_info(),
        "legs_run": sorted(legs),
        **(extra_header or {}),
    }
    off = legs.get("offline")
    if off:
        gl = off["measurements"].get("scoring", {}).get("gate_levels", list(GATE_LEVELS))
        stress = [lv for lv in LEVELS if lv not in gl]
        rep.banner = f"Gate scope: {'+'.join(gl)}" + (
            f" (D-48); {'+'.join(stress)}-level recall is reported but not gated" if stress else ""
        )
        rep.header["dataset"] = off["measurements"].get("dataset", {})
        rep.header["scoring"] = off["measurements"].get("scoring", {})
        rep.header["offline_run"] = off.get("header", {})
        rep.header["seeded_defect"] = (off["measurements"].get("run") or {}).get("seeded_defect")
    if gate_results:
        rep.header["commands"] = [s["command"] for s in gate_results.get("steps", [])]
        rows = []
        for s in gate_results.get("steps", []):
            rep.checks.append(
                Check(f"step: {s['name']}", float(s["mapped_rc"]), "==", 0.0, note=s.get("note", ""))
            )
            rows.append([s["name"], s["rc"], s["mapped_rc"], round(s["seconds"], 1)])
        rep.tables.append(Table("Gate steps", ["step", "raw exit", "gate exit", "seconds"], rows))
    rep.data["rows"] = {}
    if off:
        tables, data, notes = offline_tables(off)
        rep.tables += tables
        rep.data["rows"] = {k: v for k, v in data.items() if k in {r.key for r in ALL_ROWS}}
        rep.data["flicker"] = data["flicker"]
        rep.data["detectors"] = data["detectors"]
        rep.data["lanes_ms"] = data["lanes_ms"]
        rep.data["lanes_ms_production"] = data.get("lanes_ms_production", {})
        rep.notes += notes
        rep.failing_ids = list(off["measurements"].get("failing_ids", []))
        run = off["measurements"].get("run", {})
        rep.notes.append(
            f"offline run: {run.get('seconds')} s, lane runs {run.get('lane_runs')}, detector exceptions "
            f"{run.get('detect_errors')}, clip frames {run.get('clip_frames')}, slated (protect) clip frames "
            f"{run.get('protect_frames')}"
        )
        for n in run.get("notes", []):
            rep.notes.append(n)
        rep.notes.append(
            "scoring proxy: hits are mask coverage of the ground-truth quad by what the production planner emits "
            "(offline, CPU); the real-time leg measures the engine path end to end on the stamped stream only"
        )
        for c in off["checks"]:
            rep.checks.append(
                Check(
                    f"offline: {c['name']}",
                    c["value"], c["op"], c["target"], c.get("unit", ""), c.get("note", ""), bool(c.get("not_run")),
                )
            )  # fmt: skip
    for name in ("perf-cpu", "perf-gpu"):
        leg = legs.get(name)
        if not leg:
            continue
        for c in leg.get("checks", []):
            rep.checks.append(
                Check(
                    f"{name}: {c['name']}",
                    c["value"], c["op"], c["target"], c.get("unit", ""), c.get("note", ""), bool(c.get("not_run")),
                )
            )  # fmt: skip
        m = leg["measurements"]
        rep.data.setdefault("perf", {})[name] = {
            "fps": m["fps"]["fps"],
            "delay_p95_s": m["delay_tap"]["p95_s"],
            "cpu_mean_cores": (m.get("cpu") or {}).get("mean_cores"),
            "vram_over_idle_mb": (m.get("vram_mb") or {}).get("peak_over_baseline_mb"),
        }
    rep.tables += perf_tables(legs)
    bpath = baseline_path or ROOT / "eval" / "baselines" / "phase2.json"
    if bpath.exists() and rep.data.get("rows"):
        base = json.loads(bpath.read_text(encoding="utf-8"))
        reg = []
        for k, v in base.get("rows", {}).items():
            now = rep.data["rows"].get(k)
            if not now:
                continue
            for s in ("stills", "clips"):
                b, c = v.get(s), now[s]["recall"]
                if b is not None and c is not None:
                    reg.append([k, s, _f(b), _f(c), _f(c - b)])
        rep.tables.append(
            Table(
                "Regression vs eval/baselines/phase2.json (informational)",
                ["row", "set", "baseline", "now", "delta"],
                reg,
            )
        )
    return rep


def run(
    out_dir: Path,
    results: Path | None,
    date: str | None,
    report_dir: Path | None,
    write_baseline: bool = True,
    phase: str = "phase2",
) -> int:
    gate = json.loads(results.read_text(encoding="utf-8")) if results and results.exists() else None
    rep = build_report(out_dir, gate, date, phase)
    md, _js = write_report(rep, report_dir or ROOT / "eval" / "reports")
    print(f"report: {md}")
    bpath = ROOT / "eval" / "baselines" / "phase2.json"
    if rep.passed and write_baseline and not bpath.exists() and rep.data.get("rows"):
        bpath.parent.mkdir(parents=True, exist_ok=True)
        bpath.write_text(
            json.dumps(baseline_values(rep.data), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"baseline written: {bpath}")
    return 0 if rep.passed else 1


__all__ = ["ROWS", "build_report", "run"]
