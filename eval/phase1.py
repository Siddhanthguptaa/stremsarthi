"""Gate G1 legs (docs/PLAN.md section 5, docs/specs/eval.md 5.9): analysis and orchestration.

The ``analyse_*`` functions are pure (tap frames and numbers in, checks out) and unit-tested on synthetic data.
The ``run_*`` functions drive ``eval.stack.run_chain`` and write ``<out>/<leg>.json``; the exit code of a leg is
0 pass, 1 a criterion failed, 2 environment problem or missing hooks (nothing trustworthy measured), 3 a
required hardware leg was skipped.

Legs
* ``main``      120 s run (card absent 10 s, static 40 s, moving 40 s, absent 30 s): delay, fps, card
                unreadable, A/V offset, stamp readability, protect fraction.
* ``failsafe``  ``SS_FAULT=lane:accurate:hang@t=20s`` while a card is held: every frame captured more than
                1.0 s after the first missed deadline is a slate, and no frame shows the card.
* ``hold``      hold sent over the real bus while the card is visible: slate within 1 frame, retro window
                covered, audio silent, release returns to LIVE only on newer frames (fence).
* ``soak``      N minutes: memory growth, fps, delay percentiles, frames showing the card (must be 0).
* ``live``      3 minutes to an unlisted YouTube broadcast (needs ``YOUTUBE_STREAM_KEY``).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from . import fault as faultlib
from .cardcheck import ocr_available
from .delay import (
    align_to_engine,
    delay_stats,
    egress_delays,
    encoder_pts_map,
    engine_frames,
    fps_stats,
    gap_stats,
    live_frames,
    read_engine_timing,
    write_tap_timing,
)
from .harness.procs import find_mediamtx
from .harness.report import Check, git_rev, hardware_info
from .harness.timing import stage_latencies
from .holdbus import HoldClient
from .stack import Action, ChainResult, ChainSpec, EnvError, gpu_present, result_json, run_chain
from .tap import card_stats, stamp_stats

ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_FAIL, EXIT_ENV, EXIT_HW = 0, 1, 2, 3

MAIN_SCRIPT = "card_absent:10,card_static:40,card_moving:40,card_absent:30"
MAIN_SECONDS = 120.0
FAILSAFE_SCRIPT = "card_absent:10,card_hold:60"
FAILSAFE_FAULT = "lane:accurate:hang@t=20s"
FAILSAFE_SECONDS = 50.0
HOLD_SCRIPT = "card_absent:10,card_hold:60"
HOLD_SECONDS = 58.0
SOAK_SCRIPT = "card_hold:40,card_moving:20,card_absent:10"
LIVE_SCRIPT = "card_absent:10,card_static:60,card_hold:60,card_absent:50"
LIVE_SECONDS = 180.0

DELAY_P95_S = 5.0
DELAY_MAX_S = 5.5
FPS_MIN = 29.0
CARD_RECALL_MIN = 0.98
AV_OFFSET_MAX_MS = 40.0
STAMP_UNREADABLE_MAX = 0.01
PROTECT_FRACTION_MAX = 0.10
LANE_MAX_AGE_S = 12 / 30  # accurate lane max age (engine.lane_max_age.accurate frames at 30 fps)
FAILSAFE_WITHIN_S = 1.0
HOLD_MAX_LIVE_FRAMES = 1
SILENCE_DBFS = -50.0
RSS_GROWTH_MAX_PCT = 10.0


def chk(name: str, value: float | None, op: str, target: float, unit: str = "", note: str = "") -> Check:
    return Check(name, value, op, target, unit, note)


def _pct(values: Sequence[float], q: float) -> float | None:
    return round(float(np.percentile(np.asarray(values), q)), 4) if len(values) else None


# ---------------------------------------------------------------- shared measurements


def common_measurements(res: ChainResult, warmup_s: float = 1.0) -> dict[str, Any]:
    frames = res.tap.frames
    eng_log = read_engine_timing(res.engine_timing_path)
    align = align_to_engine(frames, engine_frames(eng_log), enc_map=encoder_pts_map(eng_log))
    egress = egress_delays(frames, eng_log, warmup_s=warmup_s)
    stamped = [f for f in frames if f.kind == "live"]
    window = [f for f in frames if stamped and stamped[0].t_read <= f.t_read <= stamped[-1].t_read]
    slates = sum(1 for f in window if f.kind == "slate")
    return {
        "delay_tap": delay_stats(frames, warmup_s),
        "delay_egress": {
            "n": len(egress),
            "p50_s": _pct(egress, 50),
            "p95_s": _pct(egress, 95),
            "max_s": round(max(egress), 4) if egress else None,
        },
        "fps": fps_stats(frames),
        "gaps": gap_stats(frames),
        "card": card_stats(frames, warmup_s),
        "stamp": stamp_stats(res.tap),
        "protect_fraction": (slates / len(window)) if window else None,
        "engine_alignment": align,
        "stage_latency_ms": stage_latencies(eng_log) if eng_log.timings else {},
        "reader": res.tap.reader.summary(),
        "cpu": res.cpu,
        "vram_mb": res.vram_mb,
        "tap_errors": res.tap.errors,
    }


def _alive_check(res: ChainResult) -> Check:
    return chk(
        "engine alive at end of run",
        1.0 if res.engine_exit is None else 0.0,
        "==",
        1.0,
        note=f"exit={res.engine_exit}",
    )


# ---------------------------------------------------------------- main leg


def analyse_main(res: ChainResult, label: str) -> tuple[list[Check], dict[str, Any]]:
    m = common_measurements(res)
    card, d, eg = m["card"], m["delay_tap"], m["delay_egress"]
    av = m["reader"]["av_offset_ms"]
    checks = [
        _alive_check(res),
        chk(f"[{label}] delay p95 at tap", d["p95_s"], "<=", DELAY_P95_S, "s", f"n={d['n']}"),
        chk(f"[{label}] delay max at tap", d["max_s"], "<=", DELAY_MAX_S, "s"),
    ]
    if eg["n"]:
        checks += [
            chk(
                f"[{label}] delay p95 at egress socket (D-1)",
                eg["p95_s"],
                "<=",
                DELAY_P95_S,
                "s",
                f"n={eg['n']}",
            ),
            chk(f"[{label}] delay max at egress socket", eg["max_s"], "<=", DELAY_MAX_S, "s"),
        ]
    checks += [
        chk(f"[{label}] output fps", m["fps"]["fps"], ">=", FPS_MIN, "fps"),
        chk(f"[{label}] card-visible frames scored", float(card["visible_frames"]), ">=", 600.0, "frames"),
        chk(
            f"[{label}] card unreadable, frame recall",
            card["unreadable_recall"],
            ">=",
            CARD_RECALL_MIN,
            note=f"CI {card.get('recall_ci')}",
        ),
        chk(
            f"[{label}] Verhoeff-valid OCR reads of the card",
            float(card["ocr_valid_reads"]),
            "==",
            0.0,
            "reads",
            f"{card['ocr_checked']} OCR checks",
        ),
        chk(f"[{label}] OCR checks run", float(card["ocr_checked"]), ">=", 10.0, "frames"),
        chk(f"[{label}] A/V offset max", av["max_abs_ms"], "<=", AV_OFFSET_MAX_MS, "ms", f"n={av['n']}"),
        chk(
            f"[{label}] stamp unreadable (not slates)",
            m["stamp"]["stamp_unreadable_frac"],
            "<=",
            STAMP_UNREADABLE_MAX,
        ),
        chk(
            f"[{label}] protect (slate) fraction while live",
            m["protect_fraction"],
            "<=",
            PROTECT_FRACTION_MAX,
        ),
    ]
    return checks, m


# ---------------------------------------------------------------- fail-safe leg


def analyse_failsafe(
    res: ChainResult, spec: faultlib.FaultSpec, t_fault: float | None
) -> tuple[list[Check], dict[str, Any]]:
    m = common_measurements(res)
    frames = res.tap.frames
    if t_fault is None:
        return [
            chk(
                "fault activation recorded (hooks present)",
                0.0,
                "==",
                1.0,
                note="no fault_active record in SS_FAULT_LOG",
            )
        ], m
    deadline_miss = t_fault + LANE_MAX_AGE_S  # capture time of the first frame the dead lane no longer covers
    t_slate_from = deadline_miss + FAILSAFE_WITHIN_S
    live_after = [f for f in live_frames(frames) if (f.capture_s or 0.0) >= t_slate_from]
    leaked_after = [
        f for f in frames if f.kind in ("live", "slate") and f.leaked and (f.capture_s or t_fault) >= t_fault
    ]
    slates_after = [f for f in frames if f.kind == "slate" and f.t_read >= t_fault]
    card = m["card"]
    m["failsafe"] = {
        "t_fault_monotonic": t_fault,
        "deadline_miss_capture_s": deadline_miss,
        "live_frames_after_window": len(live_after),
        "first_live_after_window_counter": live_after[0].counter if live_after else None,
        "leaked_frames_after_fault": len(leaked_after),
        "slate_frames_after_fault": len(slates_after),
    }
    checks = [
        _alive_check(res),
        chk("fault activation recorded (hooks present)", 1.0, "==", 1.0),
        chk(
            "non-slate frames captured > 1.0 s after the first missed deadline",
            float(len(live_after)),
            "==",
            0.0,
            "frames",
        ),
        chk("frames showing the card after the fault", float(len(leaked_after)), "==", 0.0, "frames"),
        chk(
            "frames showing the card (whole run, after warm-up)",
            float(card["leaked_frames"]),
            "==",
            0.0,
            "frames",
        ),
        chk("slate frames seen after the fault", float(len(slates_after)), ">=", 30.0, "frames"),
    ]
    return checks, m


# ---------------------------------------------------------------- hold leg


def analyse_hold(
    res: ChainResult, t_hold: float | None, t_release: float | None, retro_s: float, fps: float = 30.0
) -> tuple[list[Check], dict[str, Any]]:
    m = common_measurements(res)
    frames = res.tap.frames
    if t_hold is None or t_release is None:
        return [chk("hold and release were sent over the bus", 0.0, "==", 1.0)], m
    live = live_frames(frames)
    aligned = m["engine_alignment"]["valid"]
    # spec 5.9: "within 1 frame of the command's engine-receipt time". When the engine logs the receipt
    # (event "hold_received", see the hook list in eval/fault.py) use it; otherwise the time of the bus send.
    receipts = [
        float(e["t"])
        for e in read_engine_timing(res.engine_timing_path).events
        if e.get("name") == "hold_received" and t_hold <= float(e.get("t", 0.0)) <= t_hold + 1.0
    ]
    t_receipt = min(receipts) if receipts else None
    if t_receipt is not None:
        t_hold_eff = t_receipt
    else:
        t_hold_eff = t_hold
    out: dict[str, Any] = {"t_hold": t_hold, "t_release": t_release, "engine_alignment_valid": aligned}
    checks: list[Check] = [_alive_check(res)]

    # slate onset and release as seen at the tap
    slate_after = [f for f in frames if f.kind == "slate" and f.t_read > t_hold]
    s0 = min((f.t_read for f in slate_after), default=None)
    live_after_s0 = [f for f in live if s0 is not None and f.t_read > s0]
    s1 = min((f.t_read for f in live_after_s0), default=None)
    out["first_slate_read_after_hold_s"] = None if s0 is None else round(s0 - t_hold, 4)
    out["first_live_read_after_release_s"] = None if s1 is None else round(s1 - t_release, 4)

    if aligned:
        emitted = [(f, f.extra.get("engine_emitted")) for f in live]
        during = [f for f, e in emitted if e is not None and t_hold_eff < e < t_release]
        # frames the engine emitted live after the command: the first may already have been in flight
        out["live_frames_emitted_after_hold"] = len(during)
        out["hold_reference"] = "engine_receipt_event" if t_receipt is not None else "bus_send_time"
        out["live_emit_offsets_after_hold_ms"] = sorted(
            round((e - t_hold) * 1000.0, 1) for f, e in emitted if e is not None and t_hold < e < t_release
        )[:4]
        checks.append(
            chk(
                "live frames emitted after the hold command (slate within 1 frame)",
                float(len(during)),
                "<=",
                float(HOLD_MAX_LIVE_FRAMES),
                "frames",
            )
        )
        old_after_release = [
            f for f, e in emitted if e is not None and e >= t_release and (f.capture_s or 0.0) <= t_hold
        ]
        out["older_than_hold_live_after_release"] = len(old_after_release)
        checks.append(
            chk(
                "frames captured before the hold shown live after release (fence)",
                float(len(old_after_release)),
                "==",
                0.0,
                "frames",
            )
        )
    else:
        note = "engine timing log did not align with the tap (needs engine_delay stage, frame PTS preserved)"
        checks.append(
            chk(
                "live frames emitted after the hold command (slate within 1 frame)",
                None,
                "<=",
                float(HOLD_MAX_LIVE_FRAMES),
                "frames",
                note,
            )
        )
        checks.append(
            chk(
                "frames captured before the hold shown live after release (fence)",
                None,
                "==",
                0.0,
                "frames",
                note,
            )
        )

    ingest = m["engine_alignment"]["ingest_s_median"] or 0.3
    retro_live = [
        f for f in live if (f.capture_s or 0.0) > t_hold - retro_s + 0.05 and (f.capture_s or 0.0) <= t_hold
    ]
    # a frame inside the window can only be on screen if it was emitted before the command; those are not violations
    if aligned:
        retro_live = [f for f in retro_live if (f.extra.get("engine_emitted") or 0.0) > t_hold]
    else:
        retro_live = [
            f for f in retro_live if f.t_read - (f.delay_s or 0.0) + res.spec.delay_s + ingest > t_hold
        ]
    out["retro_window_s"] = retro_s
    out["live_frames_in_retro_window"] = len(retro_live)
    checks.append(
        chk(
            f"live frames captured in the last {retro_s:g} s before the hold",
            float(len(retro_live)),
            "==",
            0.0,
            "frames",
        )
    )

    # audio silent while the slate is up
    if s0 is not None:
        w0, w1 = s0 + 0.3, (s1 if s1 is not None else t_release) - 0.3
        win = [db for t, db in res.tap.audio if w0 <= t <= w1]
        out["audio_frames_in_hold"] = len(win)
        out["audio_max_dbfs_in_hold"] = None if not win else round(max(win), 2)
        checks.append(chk("audio frames measured during the hold", float(len(win)), ">=", 20.0, "frames"))
        checks.append(
            chk(
                "audio level during the hold (max)", None if not win else max(win), "<=", SILENCE_DBFS, "dBFS"
            )
        )
    else:
        checks.append(
            chk(
                "audio frames measured during the hold",
                0.0,
                ">=",
                20.0,
                "frames",
                "no slate, so no hold window",
            )
        )

    checks.append(
        chk(
            "LIVE again after release (seconds to first live frame)",
            out["first_live_read_after_release_s"],
            "<=",
            10.0,
            "s",
        )
    )
    checks.append(chk("a slate appeared after the hold command", 0.0 if s0 is None else 1.0, "==", 1.0))
    checks.append(
        chk(
            "frames showing the card (whole run, after warm-up)",
            float(m["card"]["leaked_frames"]),
            "==",
            0.0,
            "frames",
        )
    )
    m["hold"] = out
    return checks, m


# ---------------------------------------------------------------- soak leg


class MemTrace:
    """Process-tree RSS and thread count sampled every few seconds."""

    def __init__(self, pid: int, interval_s: float = 5.0) -> None:
        import threading

        self.pid, self.interval_s = pid, interval_s
        self.samples: list[tuple[float, float, int]] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _read(self) -> tuple[float, int]:
        rss = thr = 0
        try:
            root = psutil.Process(self.pid)
            for p in [root, *root.children(recursive=True)]:
                try:
                    rss += p.memory_info().rss
                    thr += p.num_threads()
                except psutil.Error:
                    continue
        except psutil.Error:
            pass
        return rss / 1e6, thr

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            rss, thr = self._read()
            self.samples.append((time.monotonic(), rss, thr))

    def start(self) -> MemTrace:
        self._t.start()
        return self

    def stop(self) -> list[tuple[float, float, int]]:
        self._stop.set()
        self._t.join(timeout=3)
        return self.samples


def rss_growth_pct(samples: Sequence[tuple[float, float, int]], warmup_s: float = 60.0) -> dict[str, Any]:
    """Growth of RSS from the first minute after warm-up to the last minute (percent of the first)."""
    if len(samples) < 6:
        return {"growth_pct": None, "n": len(samples)}
    t0 = samples[0][0]
    body = [s for s in samples if s[0] - t0 >= warmup_s] or list(samples)
    k = max(1, min(len(body) // 4, int(60 / 5)))
    first = float(np.mean([s[1] for s in body[:k]]))
    last = float(np.mean([s[1] for s in body[-k:]]))
    thr_first = float(np.mean([s[2] for s in body[:k]]))
    thr_last = float(np.mean([s[2] for s in body[-k:]]))
    return {
        "growth_pct": round((last - first) / first * 100.0, 2) if first > 0 else None,
        "first_mb": round(first, 1),
        "last_mb": round(last, 1),
        "threads_first": round(thr_first, 1),
        "threads_last": round(thr_last, 1),
        "n": len(samples),
    }


def analyse_soak(res: ChainResult, mem: dict[str, Any], minutes: float) -> tuple[list[Check], dict[str, Any]]:
    m = common_measurements(res)
    card, d = m["card"], m["delay_tap"]
    m["memory"] = mem
    checks = [
        _alive_check(res),
        chk(
            "frames showing the card (non-protected)",
            float(card["leaked_frames"]),
            "==",
            0.0,
            "frames",
            f"{card['visible_frames']} card-visible frames",
        ),
        chk("RSS growth first to last minute", mem["growth_pct"], "<=", RSS_GROWTH_MAX_PCT, "%"),
        chk("output fps", m["fps"]["fps"], ">=", FPS_MIN, "fps"),
        chk("delay p95 at tap", d["p95_s"], "<=", DELAY_P95_S, "s", f"n={d['n']}"),
        chk("stamp unreadable (not slates)", m["stamp"]["stamp_unreadable_frac"], "<=", STAMP_UNREADABLE_MAX),
    ]
    return checks, m


# ---------------------------------------------------------------- leg runners


def _write_leg(
    out: Path,
    leg: str,
    checks: list[Check],
    measurements: dict[str, Any],
    res: ChainResult | None,
    note: str = "",
) -> int:
    passed = all(c.passed for c in checks)
    body = {
        "leg": leg,
        "passed": passed,
        "note": note,
        "checks": [asdict(c) | {"passed": c.passed} for c in checks],
        "measurements": measurements,
        "run": result_json(res) if res is not None else None,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{leg}.json").write_text(
        json.dumps(body, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return EXIT_OK if passed else EXIT_FAIL


def _print_checks(leg: str, checks: list[Check]) -> None:
    print(f"leg {leg}:")
    for c in checks:
        v = "n/a" if c.value is None else f"{c.value:.4g}"
        print(
            f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {v} {c.op} {c.target:g} {c.unit} {('(' + c.note + ')') if c.note else ''}"
        )


def _save_timing(res: ChainResult, out: Path, leg: str, profile: str = "dev") -> None:
    write_tap_timing(
        out / f"{leg}-tap-timing.jsonl",
        res.tap.frames,
        run_id=f"{leg}-{time.strftime('%Y%m%d-%H%M%S')}",
        profile=profile,
        git_rev=git_rev(),
        t0_wall=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        hardware=hardware_info(),
        seed=1234,
        events=res.events,
    )


def preflight_problems(engine: str, root: Path = ROOT) -> list[str]:
    """What is missing before a chain can be built (each entry is one blocking problem)."""
    problems: list[str] = []
    if shutil.which("ffmpeg") is None:
        problems.append("ffmpeg is not on PATH (run through scripts/ss.sh)")
    if find_mediamtx() is None:
        problems.append("MediaMTX binary not found (scripts/fetch_tools.sh)")
    if not ocr_available():
        problems.append("rapidocr is not installed: the card checker cannot run its OCR test")
    try:
        import zmq  # noqa: F401
    except ImportError:
        problems.append("pyzmq is not installed: the hold test cannot use the bus")
    if engine == "real":
        for rel in ("engine/main.py", "relay/runtime.py", "vision/host.py"):
            if not (root / rel).exists():
                problems.append(f"{rel} is missing")
        runtime = root / "relay" / "runtime.py"
        if runtime.exists() and "NullFrameSink()" in runtime.read_text(encoding="utf-8", errors="ignore"):
            problems.append(
                "relay has no encoder/egress yet (relay/runtime.py still returns NullFrameSink, task T1.2), "
                "so nothing is published on the `shielded` tap and there is nothing to measure"
            )
    return problems


def run_preflight(out: Path, engine: str) -> int:
    problems = preflight_problems(engine)
    out.mkdir(parents=True, exist_ok=True)
    (out / "preflight.json").write_text(
        json.dumps({"engine": engine, "problems": problems}, indent=2) + "\n", encoding="utf-8"
    )
    if problems:
        print(f"ENV ERROR: gate 1 cannot run the '{engine}' chain:")
        for p in problems:
            print(f"  - {p}")
        return EXIT_ENV
    print(f"preflight ok ({engine} chain)")
    return EXIT_OK


def run_main(out: Path, engine: str, gpu: bool, seconds: float = MAIN_SECONDS) -> int:
    label = "gpu" if gpu else "cpu"
    leg = f"main-{label}"
    if gpu and not gpu_present():
        print("no GPU found: the GPU leg cannot run")
        return EXIT_HW
    try:
        res = run_chain(ChainSpec(out / leg, MAIN_SCRIPT, seconds, engine=engine, gpu=gpu))
    except EnvError as exc:
        print(f"ENV ERROR: {exc}")
        return EXIT_ENV
    _save_timing(res, out, leg)
    if res.tap.errors:
        print(f"tap errors: {res.tap.errors}")
    checks, m = analyse_main(res, label)
    _print_checks(leg, checks)
    return _write_leg(out, leg, checks, m, res)


def hooks_missing_message() -> str | None:
    # the engine has no hook of its own (it must only fail closed); the lane hook lives in vision/lanes.py
    present = faultlib.hooks_present(ROOT, ("vision",))
    if not all(present.values()):
        missing = [k for k, v in present.items() if not v]
        return f"SS_FAULT hooks missing in {', '.join(missing)} (see eval/fault.py for the protocol)"
    return None


def run_failsafe(out: Path, engine: str, fault_text: str = FAILSAFE_FAULT) -> int:
    spec = faultlib.parse_spec(fault_text)
    if engine == "real":
        msg = hooks_missing_message()
        if msg:
            print(f"ENV ERROR: {msg}")
            return EXIT_ENV
    try:
        res = run_chain(
            ChainSpec(
                out / "failsafe", FAILSAFE_SCRIPT, FAILSAFE_SECONDS, engine=engine, fault=spec.canonical()
            )
        )
    except EnvError as exc:
        print(f"ENV ERROR: {exc}")
        return EXIT_ENV
    t_fault = faultlib.activation_time(faultlib.read_fault_log(res.fault_log_path), spec)
    _save_timing(res, out, "failsafe")
    checks, m = analyse_failsafe(res, spec, t_fault)
    _print_checks("failsafe", checks)
    if t_fault is None:
        _write_leg(out, "failsafe", checks, m, res, note="hooks missing")
        return EXIT_ENV
    return _write_leg(out, "failsafe", checks, m, res)


def run_hold(
    out: Path, engine: str, hold_at_s: float = 25.0, hold_for_s: float = 8.0, retro_s: float = 3.0
) -> int:
    client = HoldClient()
    stamps: dict[str, float] = {}

    def do_hold() -> None:
        if not client.connect(5.0):
            raise RuntimeError("no engine subscribed to control.hold")
        stamps["hold"] = client.send(True)

    def do_release() -> None:
        stamps["release"] = client.send(False)

    try:
        res = run_chain(
            ChainSpec(
                out / "hold", HOLD_SCRIPT, HOLD_SECONDS, engine=engine,
                actions=[Action(hold_at_s, "hold_sent", do_hold), Action(hold_at_s + hold_for_s, "release_sent", do_release)],
            )
        )  # fmt: skip
    except EnvError as exc:
        print(f"ENV ERROR: {exc}")
        return EXIT_ENV
    finally:
        client.close()
    failed = [e for e in res.events if e[0].endswith("_failed")]
    _save_timing(res, out, "hold")
    checks, m = analyse_hold(res, stamps.get("hold"), stamps.get("release"), retro_s)
    _print_checks("hold", checks)
    if failed:
        print(f"ENV ERROR: bus action failed: {failed}")
        _write_leg(out, "hold", checks, m, res, note="bus action failed")
        return EXIT_ENV
    return _write_leg(out, "hold", checks, m, res)


def run_soak(out: Path, engine: str, minutes: float, gpu: bool = False) -> int:
    if minutes <= 0:
        print("soak skipped (--minutes 0)")
        return EXIT_OK
    holder: dict[str, MemTrace] = {}
    spec = ChainSpec(out / "soak", SOAK_SCRIPT, minutes * 60.0, engine=engine, gpu=gpu, ocr_every_s=5.0)
    try:
        res = run_chain(
            spec, on_engine_start=lambda pid: holder.__setitem__("trace", MemTrace(pid, 5.0).start())
        )
    except EnvError as exc:
        print(f"ENV ERROR: {exc}")
        return EXIT_ENV
    samples = holder["trace"].stop() if "trace" in holder else []
    mem = rss_growth_pct(samples)
    _save_timing(res, out, "soak")
    checks, m = analyse_soak(res, mem, minutes)
    _print_checks("soak", checks)
    return _write_leg(out, "soak", checks, m, res)


def run_live(out: Path, viewer_latency_s: float | None) -> int:
    if not os.environ.get("YOUTUBE_STREAM_KEY"):
        print("SKIP live-youtube: YOUTUBE_STREAM_KEY is not set in the environment (nothing was streamed)")
        out.mkdir(parents=True, exist_ok=True)
        (out / "live.json").write_text(
            json.dumps(
                {
                    "leg": "live",
                    "skipped": True,
                    "passed": True,
                    "checks": [],
                    "note": "YOUTUBE_STREAM_KEY not set",
                }
            )
            + "\n"
        )
        return EXIT_OK
    try:
        res = run_chain(ChainSpec(out / "live", LIVE_SCRIPT, LIVE_SECONDS, engine="real", live_youtube=True))
    except EnvError as exc:
        print(f"ENV ERROR: {str(exc).replace(os.environ['YOUTUBE_STREAM_KEY'], '<key>')}")
        return EXIT_ENV
    m = common_measurements(res)
    log_text = (
        (out / "live" / "engine.log").read_text(errors="ignore")
        if (out / "live" / "engine.log").exists()
        else ""
    )
    key = os.environ["YOUTUBE_STREAM_KEY"]
    leaked_key = key in log_text
    lowered = log_text.lower()
    reconnects = lowered.count("reconnect") + lowered.count("auto_egress")
    checks = [
        _alive_check(res),
        chk("tap frames received during the live run", float(len(res.tap.frames)), ">=", 1000.0, "frames"),
        chk("egress reconnects logged", float(reconnects), "==", 0.0, note="counted from the engine log"),
        chk("stream key absent from the engine log", 0.0 if leaked_key else 1.0, "==", 1.0),
    ]
    m["viewer_latency_s"] = viewer_latency_s  # informational, never gating (D-1)
    _print_checks("live", checks)
    return _write_leg(out, "live", checks, m, res, note="viewer-side latency is informational")
