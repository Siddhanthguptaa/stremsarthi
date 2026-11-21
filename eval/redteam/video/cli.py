"""``python -m eval.run redteam-video``: run the video red team, write the report, save repro frames.

Exit codes: 0 ran (and no regression vs the baseline, when one exists), 1 a leak-rate regression of more than 5 points
or the seeded-defect self-check failed, 2 environment error (no detector ran, nothing was measured).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import cv2

from ...harness.report import git_rev, hardware_info
from . import attacks, pipeline, readback, report, score
from .model import EXPOSED_MIN_IN_FRAME, EXPOSED_MIN_SIDE_PX, Case, Frame

ROOT = Path(__file__).resolve().parents[3]
EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2
DEFAULT_OUT = Path("/tmp/ss_redteam_video")
BASELINE = ROOT / "eval" / "baselines" / "redteam_video.json"

LIMITS = [
    "Text attacks (fonts, mixed script, split lines, separators) all show 0 leaks. The leaking frames of other attacks show the text detector emitting `signboard` masks over every text box, which is consistent with the dev profile masking all text rather than recognising PII; these attacks therefore mostly measure that blanket rule, and will start to matter when it is narrowed (not isolated in this run).",
    "Faces are scored by the coverage proxy only (no output-side face check); `face_turned` is a frontal portrait foreshortened by yaw, not a true profile view (no profile images are available).",
    "The read-back is OCR (RapidOCR PP-OCR, Latin/Chinese model) and zxing; it under-reads Indic digits and stylised fonts, so for those attacks the proxy leak is the stronger signal and `src-unreadable` is expected.",
    "Frame drops model a dropped frame as a lane run that does not happen on that capture slot; the engine's own sequencing under real drops is covered by the Phase 1/7 stream tests, not here.",
    "Offline and single-process: lane latency is not applied (detector results are as fresh as the schedule allows), so real-time leaks caused by slow lanes are not visible here.",
]


# ---------------------------------------------------------------------------------------------- repro frames
class ReproKeeper:
    """Keeps the worst frame of the current case (confirmed > readable proxy leak > unreadable proxy leak)."""

    def __init__(self) -> None:
        self.best: tuple[int, int, Frame, list[Any], list[dict[str, Any]]] | None = None

    def reset(self) -> None:
        self.best = None

    def __call__(self, k: int, f: Frame, masks: list[Any], protect: bool, rows: list[dict[str, Any]]) -> None:
        if protect:
            return
        for r in rows:
            if not score.is_leak(r):
                continue
            pri = 3 if score.is_confirmed(r) else {"src_readable": 2, "src_unreadable": 0}.get(r.get("ro"), 1)
            if self.best is None or pri > self.best[0]:
                self.best = (pri, k, f, masks, [r])
                return

    def save(self, case: Case, case_dir: Path, styles: dict[str, Any] | None) -> str | None:
        if self.best is None:
            return None
        _, k, f, masks, rows = self.best
        case_dir.mkdir(parents=True, exist_ok=True)
        stem = f"f{k:03d}"
        cv2.imwrite(str(case_dir / f"{stem}_input.png"), f.image)
        cv2.imwrite(
            str(case_dir / f"{stem}_output.png"), readback.paint_output(f.image, masks, False, styles)
        )
        r = rows[0]
        meta = {
            "case": case.id,
            "attack": case.attack,
            "param": case.param,
            "params": case.params,
            "seed": case.seed,
            "frame": k,
            "target": {kk: r.get(kk) for kk in ("j", "cat", "cov", "proxy", "ro", "tag")},
            "quad": next(t["quad"] for t in f.truth["targets"] if t["j"] == r["j"]),
            "masks": [
                {"category": m.category, "quad": [list(p) for p in m.quad], "detector": m.detector}
                for m in masks
            ],
            "note": "SPECIMEN synthetic frame; regenerate with the command below",
            "regen": f"python -m eval.run redteam-video --attacks {case.attack} --seed {case.seed} --no-report",
        }
        (case_dir / f"{stem}_meta.json").write_text(
            json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8"
        )
        return str(case_dir / stem)


# ---------------------------------------------------------------------------------------------- real run
def _detector_summary(dset: Any) -> str:
    return ", ".join(f"{i}={s.state}" for i, s in sorted(dset.status.items()))


def rescore(a: argparse.Namespace) -> int:
    """Re-aggregate ``<out>/records.jsonl`` of an earlier run (no detectors) with the current scoring code."""
    out_dir = Path(a.out)
    recs_path = out_dir / "records.jsonl"
    date = a.date or time.strftime("%Y-%m-%d")
    prev_path = (
        Path(a.report_dir) if a.report_dir else ROOT / "eval" / "reports"
    ) / f"{date}-{a.report_name}.json"
    if not recs_path.exists() or not prev_path.exists():
        print(f"ENV ERROR: need {recs_path} and the earlier report {prev_path}")
        return EXIT_ENV
    records = [
        json.loads(line) for line in recs_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    prev = json.loads(prev_path.read_text(encoding="utf-8"))
    repro = {
        d.name: str(next(iter(sorted(d.glob("f*_input.png"))), d / "?")).removesuffix("_input.png")
        for d in (out_dir / "repro").glob("*")
        if d.is_dir()
    }
    sel = [
        Case(c, c.split(".")[0], "", "", {}, a.seed, lambda: []) for c in sorted({r["case"] for r in records})
    ]
    h = prev["header"]

    class _D:  # the detector summary is kept from the earlier report
        status: dict[str, Any] = {}

    res_dset = _D()
    code = finish(
        a,
        sel,
        records,
        prev["skipped"],
        prev["empty"],
        prev["protect_by_attack"],
        repro,
        res_dset,
        h["readback"],
        (prev.get("seeded") or [None])[0],
        h["seconds"],
        h,
    )
    return code


def run(a: argparse.Namespace) -> int:
    from contracts import Config

    from ...phase2.detectors import load_detectors

    sel = attacks.cases(a.seed, a.attacks, a.groups, a.quick, a.max_cases)
    skipped = attacks.skipped(a.attacks)
    if not sel:
        print("ENV ERROR: no case selected (check --attacks/--groups)")
        return EXIT_ENV
    cfg = Config(profile="dev", _env_file=None)
    try:
        cv2.setNumThreads(int(cfg.engine.cv_threads or 2))
    except Exception:  # noqa: BLE001
        pass
    print(f"{len(sel)} cases; loading detectors ...", flush=True)
    dset = load_detectors(cfg)
    for i, s in sorted(dset.status.items()):
        print(f"  detector {i}: {s.reason()}", flush=True)
    if not dset.detectors:
        print("ENV ERROR: no detector could be loaded: nothing was measured")
        dset.close()
        return EXIT_ENV
    seeded_note = None
    if a.seeded_defect:
        from ...phase2 import seeded

        try:
            seeded_note = seeded.apply(dset, a.seeded_defect)
        except seeded.SeededDefectError as exc:
            print(f"ENV ERROR: {exc}")
            dset.close()
            return EXIT_ENV
        print("  " + seeded_note, flush=True)
    why = None if a.no_readback else readback.ocr_available()
    reader = None if (a.no_readback or why) else readback.Reader()
    ro_note = "off (--no-readback)" if a.no_readback else (why or "zxing-cpp (QR) + RapidOCR (text)")
    styles = readback.styles_from_config(cfg)
    source = pipeline.DetectorSource(dset, cfg)
    out_dir = Path(a.out)
    repro_root = out_dir / "repro"
    records: list[dict[str, Any]] = []
    repro: dict[str, str] = {}
    empty: list[str] = []
    protect_by_attack: dict[str, int] = {}
    keeper = ReproKeeper()
    t_start = time.time()
    try:
        for n, case in enumerate(sel, 1):
            keeper.reset()
            frames = case.build()
            recs, info = pipeline.run_case(
                case, source, reader, styles, stride=a.readback_stride, frames=frames, on_frame=keeper
            )
            del frames
            records += recs
            protect_by_attack[case.attack] = protect_by_attack.get(case.attack, 0) + info["protect"]
            if not recs:
                empty.append(case.key)
            leaks = sum(1 for r in recs if score.is_leak(r))
            path = keeper.save(case, repro_root / case.id, styles) if leaks else None
            if path:
                repro[case.id] = path
            print(
                f"  [{n}/{len(sel)}] {case.key}: scored={len(recs)} leak={leaks} dropped={info['dropped']} protect={info['protect']} {info['seconds']}s",
                flush=True,
            )
    finally:
        dset.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "records.jsonl").write_text(
        "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in records) + "\n",
        encoding="utf-8",
    )
    return finish(
        a,
        sel,
        records,
        skipped,
        empty,
        protect_by_attack,
        repro,
        dset,
        ro_note,
        seeded_note,
        time.time() - t_start,
    )


def finish(
    a: argparse.Namespace,
    sel: list[Case],
    records: list[dict[str, Any]],
    skipped: dict[str, str],
    empty: list[str],
    protect_by_attack: dict[str, int],
    repro: dict[str, str],
    dset: Any,
    ro_note: str,
    seeded_note: str | None,
    seconds: float,
    prev_header: dict[str, Any] | None = None,
) -> int:
    per_attack = score.by_attack(records)
    per_group = score.by_group(records)
    ranked = score.rank_misses(records, a.top)
    for m in ranked:
        m["repro"] = repro.get(m["worst"]["case"], "n/a")
    baseline = None
    if BASELINE.exists() and not a.no_baseline:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = score.to_baseline(per_attack, per_group)
    regs = score.regressions(current, baseline) if baseline else None
    res = {
        "header": {
            "date": a.date or time.strftime("%Y-%m-%d"),
            "git_rev": git_rev(),
            "profile": "dev",
            "seed": a.seed,
            "hardware": hardware_info(),
            "selection": ("quick " if a.quick else "")
            + (f"attacks={a.attacks} " if a.attacks else "")
            + (f"groups={a.groups}" if a.groups else "")
            or "all",
            "cases_run": len(sel),
            "frames_scored": len(records),
            "detectors": (prev_header or {}).get("detectors") or _detector_summary(dset),
            "readback": ro_note,
            "readback_stride": a.readback_stride,
            "command": "python -m eval.run redteam-video " + " ".join(a.argv),
            "exposed_in_frame": EXPOSED_MIN_IN_FRAME,
            "exposed_side": EXPOSED_MIN_SIDE_PX,
            "seconds": round(seconds, 1),
        },
        "groups": per_group,
        "attacks": per_attack,
        "ranked": ranked,
        "skipped": skipped,
        "empty": empty,
        "protect_by_attack": protect_by_attack,
        "limits": LIMITS,
        "regressions": regs,
        "baseline_current": current,
        "seeded": [seeded_note] if seeded_note else [],
    }
    if a.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE}")
    if not a.no_report:
        md, js = report.write(
            res, Path(a.report_dir) if a.report_dir else ROOT / "eval" / "reports", a.report_name
        )
        print(f"report: {md}")
    print_summary(res)
    if regs:
        print("REGRESSION vs baseline:\n  " + "\n  ".join(regs))
        return EXIT_FAIL
    return EXIT_OK


def print_summary(res: dict[str, Any]) -> None:
    print("leak per group:")
    for g, s in res["groups"].items():
        print(f"  {g:20s} {score.fmt(s['frames'])}  confirmed {score.fmt(s['confirmed'])}")
    print("ranked misses:")
    for i, m in enumerate(res["ranked"][:12], 1):
        print(f"  {i:2d}. {m['attack']}[{m['param']}] {m['kind']} leak {score.fmt(m['leak'])}")


# ---------------------------------------------------------------------------------------------- selftest
def selftest(seed: int = 1234) -> tuple[bool, list[str]]:
    """The harness must flag seeded defects: a blind detector, a displaced mask and a half-covering mask leak on a
    QR clip (zxing read-back, no OCR model needed) and on a card clip (proxy), and a perfect detector does not."""
    lines: list[str] = []
    ok = True
    reader = readback.Reader(ocr=lambda _img: "")
    qr_case = next(c for c in attacks.cases(seed, attacks=["control"]) if c.param == "qr")
    card_case = next(c for c in attacks.cases(seed, attacks=["control"]) if c.param == "aadhaar")
    qr_frames, card_frames = qr_case.build(), card_case.build()
    expect = {  # (source, max/min leak on proxy, max/min confirmed on the QR)
        "oracle": (pipeline.OracleSource(), "<=", 0.0, "<=", 0.0),
        "null": (pipeline.NullSource(), ">=", 0.99, ">=", 0.99),
        "shifted": (pipeline.ShiftedSource(1.2), ">=", 0.99, ">=", 0.99),
        "partial": (pipeline.PartialSource(0.5), ">=", 0.99, ">=", 0.0),
    }
    for name, (src, op1, v1, op2, v2) in expect.items():
        for tag, case, frames in (("qr", qr_case, qr_frames), ("card", card_case, card_frames)):
            recs, _ = pipeline.run_case(case, src, reader, None, stride=1, frames=frames)
            s = score.summarise(recs)
            leak, conf = s["frames"]["rate"] or 0.0, (s["confirmed"]["rate"] or 0.0)
            good = leak <= v1 if op1 == "<=" else leak >= v1
            if tag == "qr":
                good = good and (conf <= v2 if op2 == "<=" else conf >= v2)
            ok = ok and good
            lines.append(
                f"{'PASS' if good else 'FAIL'} {name:8s} {tag:4s} leak={leak:.2f} confirmed={conf:.2f} (n={s['frames']['n']})"
            )
    return ok, lines


# ---------------------------------------------------------------------------------------------- argv
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.run redteam-video",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--attacks", nargs="*", default=None, help=f"attack names: {', '.join(attacks.CATALOGUE)}")
    p.add_argument("--groups", nargs="*", default=None, help=f"groups: {', '.join(attacks.GROUPS)}")
    p.add_argument(
        "--quick", action="store_true", help="one or two mid-severity parameters per attack (a subset run)"
    )
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument(
        "--readback-stride", type=int, default=pipeline.READBACK_STRIDE, help="OCR/decode every Nth frame"
    )
    p.add_argument("--no-readback", action="store_true", help="coverage proxy only")
    p.add_argument(
        "--seeded-defect", default=None, help="KIND:DETECTOR (blind|crash|absent), see eval/phase2/seeded.py"
    )
    p.add_argument(
        "--selftest", action="store_true", help="prove the scoring flags seeded broken detectors (no models)"
    )
    p.add_argument("--list", action="store_true", help="list the cases and exit")
    p.add_argument(
        "--rescore",
        action="store_true",
        help="re-aggregate <out>/records.jsonl and rewrite the report (no detectors)",
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="records and repro frames (scratch, default /tmp/ss_redteam_video)",
    )
    p.add_argument("--top", type=int, default=20, help="ranked misses listed")
    p.add_argument("--report-dir", default=None)
    p.add_argument("--report-name", default="redteam-video")
    p.add_argument("--date", default=None)
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--no-baseline", action="store_true")
    p.add_argument(
        "--write-baseline", action="store_true", help="store this run as eval/baselines/redteam_video.json"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    a.argv = list(argv or [])
    if a.list:
        for c in attacks.cases(a.seed, a.attacks, a.groups, a.quick, a.max_cases):
            print(f"{c.id}\t{c.group}\t{c.params}")
        for n, why in attacks.skipped(a.attacks).items():
            print(f"NOT RUN {n}: {why}")
        return EXIT_OK
    if a.rescore:
        return rescore(a)
    if a.selftest:
        ok, lines = selftest(a.seed)
        print("\n".join(lines))
        print(
            "SELFTEST "
            + ("PASS: seeded defects are flagged" if ok else "FAIL: a seeded defect was not flagged")
        )
        return EXIT_OK if ok else EXIT_FAIL
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
