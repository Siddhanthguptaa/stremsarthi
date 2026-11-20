"""Eval CLI.

    python -m eval.run --selftest                 # same as: selftest
    python -m eval.run loopback --seconds 60      # sender -> MediaMTX -> reader smoke
    python -m eval.run ffprobe-check              # receive the sender's stream with ffprobe
    python -m eval.run gen-specimen --seed 1234 --n 5
    python -m eval.run gate-report --phase phase0 --results .tmp/gate/results.json
    python -m eval.run microbench ...             # callable latency (p50/p95/p99 JSON)
    python -m eval.run phase1 --leg main --engine real|ref|ref-broken [--gpu]   # one gate-1 leg (see eval/phase1.py)
    python -m eval.run phase1-report --dir D --results R.json   # eval/reports/<date>-phase1.md from the legs in D

Exit codes: 0 pass, 1 a criterion failed, 2 environment error (nothing measured).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2


def _run_dir(name: str) -> Path:
    run_id = f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    return ROOT / "eval" / "runs" / run_id


def cmd_selftest(_: argparse.Namespace) -> int:
    from .selftest import run_selftest

    return EXIT_OK if run_selftest() else EXIT_FAIL


def cmd_loopback(a: argparse.Namespace) -> int:
    from .loopback import EnvError, run_loopback

    run_dir = Path(a.run_dir) if a.run_dir else _run_dir("loopback")
    try:
        out = run_loopback(a.seconds, a.transport, run_dir, a.tap)
    except EnvError as exc:
        print(f"ENV ERROR: {exc}", file=sys.stderr)
        return EXIT_ENV
    r = out["reader"]
    print(
        f"loopback {a.transport}->{a.tap} {a.seconds:.0f}s: frames_read={r['frames_read']} "
        f"received={r['received_fraction']:.4f} missing={r['missing_frames']} "
        f"gaps>100ms={r['capture_gaps_over_100ms']} delay_p50={r['delay']['p50_s']}s "
        f"p95={r['delay']['p95_s']}s fps={r['fps']} av_offset_max={r['av_offset_ms']['max_abs_ms']}ms "
        f"sender_cpu_mean={out['sender_cpu']['mean_cores']} cores"
    )
    for name, ok in out["checks"].items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"  run dir: {run_dir}")
    return EXIT_OK if out["passed"] else EXIT_FAIL


def cmd_ffprobe(a: argparse.Namespace) -> int:
    from .loopback import EnvError, ffprobe_check

    run_dir = Path(a.run_dir) if a.run_dir else _run_dir("ffprobe")
    try:
        out = ffprobe_check(a.transport, run_dir)
    except EnvError as exc:
        print(f"ENV ERROR: {exc}", file=sys.stderr)
        return EXIT_ENV
    print(f"ffprobe {a.transport}: video={out['video']} audio={out['audio']}")
    for name, ok in out["checks"].items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return EXIT_OK if out["passed"] else EXIT_FAIL


def cmd_gen(a: argparse.Namespace) -> int:
    from .data_gen.common.specimen import scan_obj
    from .data_gen.video.dataset import DEFAULT_ROOT, build_dataset

    out_dir = Path(a.out) if a.out else ROOT / DEFAULT_ROOT / f"specimen_v0_seed{a.seed}"
    manifest = build_dataset(out_dir, a.seed, a.n)
    bad = scan_obj(manifest)
    print(
        f"wrote {len(manifest['items'])} items to {out_dir} manifest_sha256={manifest['manifest_sha256'][:16]}"
    )
    if bad:
        print(f"FORBIDDEN PATTERNS FOUND: {bad}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


def cmd_gate_report(a: argparse.Namespace) -> int:
    """Turn a gate's step results (JSON list written by scripts/gate_lib.sh) into reports/<date>-<phase>.md."""
    from .harness import report

    results = json.loads(Path(a.results).read_text(encoding="utf-8"))
    rep = report.Report(a.phase, a.date or time.strftime("%Y-%m-%d"))
    rep.header = {
        "git_rev": report.git_rev(),
        "profile": "dev",
        "hardware": report.hardware_info(),
        "seed": 1234,
        "commands": [r["command"] for r in results["steps"]],
    }
    rows = []
    for s in results["steps"]:
        rep.checks.append(report.Check(s["name"], float(s["mapped_rc"]), "==", 0.0, note=s.get("note", "")))
        rows.append([s["name"], s["rc"], s["mapped_rc"], round(s["seconds"], 1)])
    rep.tables.append(report.Table("Steps", ["step", "raw exit", "gate exit", "seconds"], rows))
    for att in a.attach or []:
        p = Path(att)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            rep.notes.append(f"{p.name}: " + json.dumps(data, sort_keys=True)[:2000])
    md, js = report.write_report(rep, Path(a.report_dir) if a.report_dir else ROOT / "eval" / "reports")
    print(f"report: {md}")
    return EXIT_OK


def cmd_phase1(a: argparse.Namespace) -> int:
    from . import phase1

    out = Path(a.out) if a.out else _run_dir("phase1")
    leg = a.leg
    if leg == "preflight":
        return phase1.run_preflight(out, a.engine)
    if leg == "main":
        return phase1.run_main(out, a.engine, a.gpu, a.seconds)
    if leg == "failsafe":
        return phase1.run_failsafe(out, a.engine, a.fault)
    if leg == "hold":
        return phase1.run_hold(out, a.engine)
    if leg == "soak":
        return phase1.run_soak(out, a.engine, a.minutes, a.gpu)
    return phase1.run_live(out, a.viewer_latency_s)


def cmd_phase1_report(a: argparse.Namespace) -> int:
    from . import phase1_report

    return phase1_report.run(
        Path(a.dir),
        Path(a.results) if a.results else None,
        a.date,
        Path(a.report_dir) if a.report_dir else None,
        not a.no_baseline,
        a.phase_name,
    )


def cmd_microbench(a: argparse.Namespace) -> int:
    from . import microbench

    return microbench.main(a.rest)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--selftest", action="store_true", help="run the self-test (same as the selftest command)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("selftest", help="harness self-test (CPU, offline)").set_defaults(fn=cmd_selftest)

    for name, fn in (("loopback", cmd_loopback), ("ffprobe-check", cmd_ffprobe)):
        sp = sub.add_parser(name)
        sp.add_argument("--transport", choices=["srt", "rtmp"], default="srt")
        sp.add_argument("--run-dir", default=None)
        if name == "loopback":
            sp.add_argument("--seconds", type=float, default=60.0)
            sp.add_argument(
                "--tap",
                choices=["rtmp", "rtsp", "srt"],
                default="rtmp",
                help="how the reader pulls from MediaMTX",
            )
        sp.set_defaults(fn=fn)

    sp = sub.add_parser("gen-specimen")
    sp.add_argument("--seed", type=int, default=1234)
    sp.add_argument("--n", type=int, default=5, help="items per kind")
    sp.add_argument("--out", default=None)
    sp.set_defaults(fn=cmd_gen)

    sp = sub.add_parser("gate-report")
    sp.add_argument("--phase", required=True)
    sp.add_argument("--results", required=True)
    sp.add_argument("--date", default=None)
    sp.add_argument("--report-dir", default=None)
    sp.add_argument("--attach", action="append")
    sp.set_defaults(fn=cmd_gate_report)

    sp = sub.add_parser("phase1", help="one gate-1 leg: main, failsafe, hold, soak or live")
    sp.add_argument("--leg", required=True, choices=["preflight", "main", "failsafe", "hold", "soak", "live"])
    sp.add_argument("--engine", choices=["real", "ref", "ref-broken"], default="real")
    sp.add_argument(
        "--gpu",
        action="store_true",
        help="run the engine with gpu.enabled=true under flock /tmp/streamsarthi-gpu.lock",
    )
    sp.add_argument("--seconds", type=float, default=120.0, help="main leg length")
    sp.add_argument("--minutes", type=float, default=0.0, help="soak length")
    sp.add_argument("--fault", default="lane:accurate:hang@t=20s", help="SS_FAULT value for the failsafe leg")
    sp.add_argument(
        "--viewer-latency-s", type=float, default=None, help="live leg: viewer-side latency (informational)"
    )
    sp.add_argument("--out", default=None, help="directory for the leg JSON and run data")
    sp.set_defaults(fn=cmd_phase1)

    sp = sub.add_parser("phase1-report", help="write eval/reports/<date>-phase1.md from leg JSONs")
    sp.add_argument("--dir", required=True)
    sp.add_argument("--results", default=None, help="gate step results JSON from scripts/gate_lib.sh")
    sp.add_argument("--date", default=None)
    sp.add_argument("--report-dir", default=None)
    sp.add_argument("--no-baseline", action="store_true")
    sp.add_argument(
        "--phase-name", default="phase1", help="report name (phase1-ref for harness validation runs)"
    )
    sp.set_defaults(fn=cmd_phase1_report)

    sp = sub.add_parser("microbench")
    sp.add_argument("rest", nargs=argparse.REMAINDER)
    sp.set_defaults(fn=cmd_microbench)
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if a.selftest and a.cmd is None:
        return cmd_selftest(a)
    if a.cmd is None:
        p.print_help()
        return EXIT_ENV
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
