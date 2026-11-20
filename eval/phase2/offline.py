"""Gate 2, offline accuracy leg: every detector the registry supplies, over the T2.1 images and clips, on the CPU.

``build_measurements`` and ``analyse_offline`` are pure (records and detector states in, numbers and checks out) and
unit-tested on synthetic records. ``run_offline`` loads the detectors, runs them (``runner``), writes
``<out>/offline-records.jsonl`` (compact per-target-frame records: ids, coverage, no images, no text) and
``<out>/offline.json`` (checks + measurements, read by ``phase2.report``).

Exit code of the leg: 0 every criterion met, 1 a criterion failed or is NOT RUN (a missing or broken detector is
never a pass), 2 nothing could be measured (dataset missing, vision cannot be imported, no detector ran).

Scoring scope: ``--gate-levels`` (default all of clean, light, heavy) and ``--gate-sources`` (default stills and
clips) choose what the checks use; every level and source is always reported.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..harness.report import Check, git_rev, hardware_info
from . import dataset, scoring
from .detectors import OK, DetectorSet, DetectorStatus, discover_each, load_detectors
from .rows import (
    ALL_ROWS,
    COVER_MIN,
    EXPECTED_DETECTORS,
    FLICKER_MAX,
    LEVELS,
    ROWS,
    Row,
)

EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2
SRC_LABEL = {"img": "stills", "clip": "clips"}
LEG = "offline"


@dataclass
class OfflineOptions:
    data: Path
    out: Path
    cover_min: float = COVER_MIN
    gate_levels: tuple[str, ...] = LEVELS
    gate_sources: tuple[str, ...] = ("img", "clip")
    min_n: int = 20
    min_windows: int = 20
    ocr_budget_ms: int = 5000
    fast_still_runs: int = 4
    buckets: set[str] | None = None
    max_per_bucket: int | None = None
    faces_async: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- pure analysis
def row_status(row: Row, det: dict[str, DetectorStatus]) -> tuple[bool, str]:
    """(can this row be scored, reason when not)."""
    st = det.get(row.detector)
    if st is None:
        return False, f"detector '{row.detector}' is not known to the registry"
    if not st.runs:
        return False, f"detector '{row.detector}' {st.reason()}"
    return True, ""


def build_measurements(
    records: list[dict[str, Any]],
    det_status: dict[str, DetectorStatus],
    timings_lane: dict[str, list[float]],
    timings_det: dict[str, list[float]],
    run: dict[str, Any],
    opts: OfflineOptions,
    ds_header: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for row in ALL_ROWS:
        st = scoring.row_stats(records, row, opts.cover_min, opts.gate_levels)
        ok, why = row_status(row, det_status)
        st["status"] = "ok" if ok else "NOT RUN"
        st["reason"] = why
        rows[row.key] = st
    clip_frames = int(run.get("clip_frames", 0))
    return {
        "dataset": ds_header or {},
        "scoring": {
            "cover_min": opts.cover_min,
            "gate_levels": list(opts.gate_levels),
            "gate_sources": list(opts.gate_sources),
            "min_n": opts.min_n,
            "definition": (
                "a visible target frame is a hit when the masks the production planner produces cover >= cover_min of "
                "its quad (allowlisted faces: < 10 % covered); clips use the deadline engine's nearest-result rule"
            ),
        },
        "detectors": {
            i: {"state": s.state, "detail": s.detail, "lane": s.lane, "categories": list(s.categories)}
            for i, s in sorted(det_status.items())
        },
        "rows": rows,
        "precision": scoring.precision_stats(records),
        "distractors": scoring.distractor_stats(records),
        "negatives": scoring.negative_stats(records),
        "flicker": scoring.flicker_stats(records, cover_min=opts.cover_min),
        "latency": {
            "lanes_ms": scoring.lane_latency(timings_lane),
            "detectors_ms": scoring.latency_table(timings_det),
            "note": (
                "offline, one process, one frame at a time on the CPU, OCR budget lifted "
                f"({opts.ocr_budget_ms} ms): a ceiling for the lane, not the real-time figure"
            ),
        },
        "run": run
        | {"protect_fraction_clips": (run.get("protect_frames", 0) / clip_frames) if clip_frames else None},
    }


def _recall_check(
    name: str, blk: dict[str, Any], target: float, why_not: str, min_n: int, note_extra: str
) -> Check:
    if why_not:
        return Check(name, None, ">=", target, note=f"NOT RUN: {why_not}", not_run=True)
    if blk["n"] < min_n:
        return Check(
            name,
            None,
            ">=",
            target,
            note=f"NOT RUN: only {blk['n']} visible target frames (< {min_n})",
            not_run=True,
        )
    ci = f"CI [{blk['ci_lo']:.3f}, {blk['ci_hi']:.3f}]"
    return Check(name, blk["value"], ">=", target, note=f"n={blk['n']}, {ci}{note_extra}")


def analyse_offline(m: dict[str, Any], opts: OfflineOptions) -> list[Check]:
    checks: list[Check] = []
    det = m["detectors"]
    missing = sorted(i for i in EXPECTED_DETECTORS if det.get(i, {}).get("state") != OK)
    ran = len(EXPECTED_DETECTORS) - len(missing)
    checks.append(
        Check(
            "all expected detectors loaded and ran",
            float(ran),
            "==",
            float(len(EXPECTED_DETECTORS)),
            "detectors",
            "not run: " + ", ".join(f"{i} ({det.get(i, {}).get('state', 'missing')})" for i in missing)
            if missing
            else "",
        )
    )
    errors = sum((m["run"].get("detect_errors") or {}).values())
    checks.append(
        Check(
            "detector exceptions during the run",
            float(errors),
            "==",
            0.0,
            "calls",
            f"failed lane runs: {m['run'].get('failed_lane_runs', 0)} (the engine would slate those frames)",
        )
    )
    scope = f"levels {'+'.join(opts.gate_levels)}" if set(opts.gate_levels) != set(LEVELS) else ""
    for row in ROWS:
        st = m["rows"][row.key]
        why = st["reason"] if st["status"] != "ok" else ""
        for src in opts.gate_sources:
            blk = st[src]["gate"]
            if src == "clip" and not st[src]["all"]["n"] and not why:
                continue  # the dataset has no clips for this category (listed in the report notes)
            target = float(row.target or 0.0)
            checks.append(
                _recall_check(
                    f"{row.title} recall [{SRC_LABEL[src]}]",
                    blk,
                    target,
                    why,
                    opts.min_n,
                    (", " + scope) if scope else "",
                )
            )
    fl = m["flicker"]
    if fl["windows"] < opts.min_windows:
        checks.append(
            Check(
                "mask flicker on static targets",
                None,
                "<=",
                FLICKER_MAX,
                note=f"NOT RUN: only {fl['windows']} one-second windows on static clips (< {opts.min_windows})",
                not_run=True,
            )
        )
    else:
        checks.append(
            Check(
                "mask flicker on static targets",
                fl["fraction"],
                "<=",
                FLICKER_MAX,
                note=f"{fl['flickering']}/{fl['windows']} windows, {fl['static_clips']} static clips",
            )
        )
    return checks


def failing_ids(m: dict[str, Any], checks: list[Check]) -> list[str]:
    """Ids of the missed items of every gated row whose check failed (never an image, never text)."""
    out: list[str] = []
    failed = {c.name for c in checks if not c.passed and not c.not_run}
    for row in ROWS:
        for src in ("img", "clip"):
            if f"{row.title} recall [{SRC_LABEL[src]}]" in failed:
                out += [f"{row.key}:{i}" for i in m["rows"][row.key][src]["missed"]]
    if "mask flicker on static targets" in failed:
        out += [f"flicker:{i}" for i in m["flicker"]["flickering_ids"]]
    return out


# ---------------------------------------------------------------- running
def _enrol_allowlist(dset: DetectorSet, ds: dataset.Dataset) -> list[str]:
    """Enrol the manifest's enrolment frames into the detectors' allowlist. Returns notes (no biometrics)."""
    notes: list[str] = []
    sets = ds.enroll_sets()
    st = dset.status.get("faces")
    if not sets or st is None or not st.runs:
        return notes
    try:
        from vision.detectors.faces import Enroller

        enroller = Enroller()
    except Exception as exc:  # noqa: BLE001
        return [f"face enrolment unavailable ({type(exc).__name__}): allowlisted faces cannot be scored"]
    entries: list[tuple[str, Any]] = []
    for pid, s in sorted(sets.items()):
        try:
            frames = [dataset.read_image_bgr(ds.root / f["file"]) for f in s["frames"]]
            face_id, emb = enroller.enrol(frames, pid)
            entries.append((face_id, emb))
        except Exception as exc:  # noqa: BLE001 - ValueError("too_few_faces") and file problems
            notes.append(f"enrolment of {pid} failed ({type(exc).__name__})")
    dset.resources.allowlist.update(entries)
    notes.append(f"enrolled {len(entries)}/{len(sets)} people into the allowlist")
    return notes


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def read_records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def print_checks(checks: list[Check]) -> None:
    print(f"leg {LEG}:")
    for c in checks:
        v = "n/a" if c.value is None else f"{c.value:.4g}"
        print(
            f"  [{c.status}] {c.name}: {v} {c.op} {c.target:g} {c.unit} {('(' + c.note + ')') if c.note else ''}"
        )


def dataset_matches(data: Path, seed: int, n: int, clips: int) -> tuple[bool, str]:
    """Was the dataset under ``data`` made with these parameters by the current generator? (cheap: manifest and a
    spot check of the files; regeneration is deterministic, so a mismatch just means 'generate again')"""
    from ..data_gen.video import suite

    mp = data / "manifest.json"
    if not mp.exists():
        return False, "no manifest.json"
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "manifest.json unreadable"
    if m.get("generator_version") != suite.GENERATOR_VERSION:
        return False, f"generator version {m.get('generator_version')} != {suite.GENERATOR_VERSION}"
    if m.get("seed") != seed or m.get("n_per_bucket") != n:
        return False, f"seed/n {m.get('seed')}/{m.get('n_per_bucket')} != {seed}/{n}"
    if set(m.get("buckets", {})) != set(suite.BUCKETS):
        return False, "bucket set differs"
    have_clips = int((m.get("clips") or {}).get("per_bucket", 0))
    if have_clips != clips:
        return False, f"clips per bucket {have_clips} != {clips}"
    items = m.get("items", [])
    if len(items) != n * len(suite.BUCKETS):
        return False, "item count differs"
    spot = [items[0], items[-1]] + [c for c in (m.get("clips") or {}).get("items", [])[:1]]
    for it in spot:
        rel = it.get("file") or it.get("video")
        if not rel or not (data / rel).exists():
            return False, f"file missing: {rel}"
    return True, "matches"


def preflight(data: Path) -> tuple[int, dict[str, Any]]:
    """0 when the offline leg can measure something, 2 with the reasons when it cannot. Missing detectors are
    reported (they become NOT RUN rows) but are not an environment error while at least one can run."""
    info: dict[str, Any] = {"problems": [], "detectors": {}}
    try:
        ds = dataset.load(data)
    except dataset.DatasetError as exc:
        info["problems"].append(str(exc))
        return EXIT_ENV, info
    info["problems"] += dataset.validate(ds)
    info["dataset"] = ds.header()
    try:
        classes, bad = discover_each()
    except Exception as exc:  # noqa: BLE001
        info["problems"].append(f"vision registry cannot be imported: {type(exc).__name__}")
        return EXIT_ENV, info
    for i in EXPECTED_DETECTORS:
        if i in classes:
            info["detectors"][i] = "discovered"
        elif i in bad:
            info["detectors"][i] = f"{bad[i].state} ({bad[i].detail})"
        else:
            info["detectors"][i] = "missing"
    if not classes:
        info["problems"].append("no detector package was discovered under vision/detectors/")
    return (EXIT_ENV if info["problems"] else EXIT_OK), info


def run_offline(opts: OfflineOptions) -> int:
    from contracts import Config

    from .runner import Runner, RunOptions

    try:
        ds = dataset.load(opts.data, buckets=opts.buckets, max_per_bucket=opts.max_per_bucket)
    except dataset.DatasetError as exc:
        print(f"ENV ERROR: {exc}")
        return EXIT_ENV
    problems = dataset.validate(ds)
    if problems:
        print("ENV ERROR: " + "; ".join(problems))
        return EXIT_ENV
    cfg = Config(profile="dev", _env_file=None)
    cfg.ocr.rec_budget_ms = max(cfg.ocr.rec_budget_ms, opts.ocr_budget_ms)
    try:
        import cv2

        cv2.setNumThreads(int(cfg.engine.cv_threads or 2))
    except Exception:  # noqa: BLE001
        pass
    print(
        f"dataset {ds.name}: {len(ds.images)} images, {len(ds.clips)} clips; loading detectors ...",
        flush=True,
    )
    dset = load_detectors(cfg, params={"faces": {"embed_async": opts.faces_async}})
    try:
        for i, s in sorted(dset.status.items()):
            print(f"  detector {i}: {s.reason()}", flush=True)
        if not dset.detectors:
            print("ENV ERROR: no detector could be loaded: nothing was measured")
            _write_leg(opts, [], {"detectors": dset.summary()}, "no detector loaded")
            return EXIT_ENV
        notes = _enrol_allowlist(dset, ds)
        for n in notes:
            print(f"  {n}", flush=True)
        runner = Runner(
            dset,
            cfg,
            RunOptions(
                opts.fast_still_runs, opts.buckets, opts.max_per_bucket, lambda s: print(s, flush=True)
            ),
        )
        records = runner.run(ds)
        opts.out.mkdir(parents=True, exist_ok=True)
        write_records(opts.out / "offline-records.jsonl", records)
        run_info = {
            "seconds": round(runner.stats.seconds, 1),
            "lane_runs": dict(runner.stats.lane_runs),
            "detect_calls": dict(runner.stats.detect_calls),
            "detect_errors": dict(runner.stats.detect_errors),
            "failed_lane_runs": runner.stats.failed_lane_runs,
            "protect_frames": runner.stats.protect_frames,
            "clip_frames": runner.stats.clip_frames,
            "notes": notes,
        }
        m = build_measurements(
            records,
            dset.status,
            dict(runner.timings.lane_ms),
            dict(runner.timings.detector_ms),
            run_info,
            opts,
            ds.header(),
        )
    finally:
        dset.close()
    checks = analyse_offline(m, opts)
    m["failing_ids"] = failing_ids(m, checks)
    print_checks(checks)
    return _write_leg(opts, checks, m, "")


def _write_leg(opts: OfflineOptions, checks: list[Check], measurements: dict[str, Any], note: str) -> int:
    from dataclasses import asdict

    passed = bool(checks) and all(c.passed for c in checks)
    body = {
        "leg": LEG,
        "passed": passed,
        "note": note,
        "checks": [asdict(c) | {"passed": c.passed} for c in checks],
        "measurements": measurements,
        "header": {
            "git_rev": git_rev(),
            "hardware": hardware_info(),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
    }
    opts.out.mkdir(parents=True, exist_ok=True)
    (opts.out / f"{LEG}.json").write_text(
        json.dumps(body, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return EXIT_OK if passed else EXIT_FAIL


def rescore(opts: OfflineOptions) -> int:
    """Re-score ``offline-records.jsonl`` of an earlier run (same records, same numbers) without running detectors."""
    prev = json.loads((opts.out / f"{LEG}.json").read_text(encoding="utf-8"))
    records = read_records(opts.out / "offline-records.jsonl")
    old = prev["measurements"]
    status = {
        i: DetectorStatus(i, d["state"], d["detail"], d.get("lane"), tuple(d.get("categories", ())))
        for i, d in old["detectors"].items()
    }
    # latency samples are not kept in the records: the previous table is carried over unchanged
    m = build_measurements(records, status, {}, {}, old["run"], opts, old.get("dataset"))
    m["latency"] = old["latency"]
    checks = analyse_offline(m, opts)
    m["failing_ids"] = failing_ids(m, checks)
    print_checks(checks)
    return _write_leg(opts, checks, m, "rescored from offline-records.jsonl")
