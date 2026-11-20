"""`python -m eval.run selftest`: fast CPU checks that the harness itself is sound.

No network, no GPU, no MediaMTX. Each check returns (ok, detail); the runner prints a table and the
process exits 0 only when every check passes.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .data_gen.common import specimen, verhoeff
from .data_gen.common.seeds import py_rng
from .data_gen.video import dataset
from .harness import metrics, report, timing
from .harness.gpu_lock import GpuLockTimeout, gpu_lock
from .sender import stamp
from .sender.beep import BeepTrack, detect_beeps
from .sender.roundtrip import x264_roundtrip
from .sender.scenes import SCENE_NAMES, ScenePlayer

Check = Callable[[], tuple[bool, str]]


def check_verhoeff() -> tuple[bool, str]:
    known = verhoeff.is_valid("2363") and not verhoeff.is_valid("2364") and verhoeff.check_digit("236") == 3
    rng = py_rng(1234, "selftest", "aadhaar")
    nums = [specimen.aadhaar_number(rng) for _ in range(1000)]
    ok = all(verhoeff.is_valid(n) and n[0] in "01" and len(n) == 12 for n in nums)
    pans = [specimen.pan_number(rng) for _ in range(1000)]
    pan_ok = all(p[3] == "X" and len(p) == 10 for p in pans)
    return (
        known and ok and pan_ok,
        "1000 Aadhaar (valid, leading 0/1) and 1000 PAN (4th=X); Verhoeff vector 2363",
    )


def check_stamp_roundtrip() -> tuple[bool, str]:
    g = np.random.default_rng(1234)
    frame = np.zeros((stamp.CANVAS_H, stamp.CANVAS_W, 3), dtype=np.uint8)
    bad = 0
    n = 10_000
    for counter, ms in zip(g.integers(0, 1 << 24, n), g.integers(0, stamp.MS_MODULUS, n), strict=True):
        beep = bool(counter & 1)
        stamp.draw_stamp(frame, int(counter), int(ms), beep)
        s = stamp.read_stamp(frame)
        if s is None or s.counter != counter or s.capture_ms != ms or s.beep != beep:
            bad += 1
    return bad == 0, f"{n} random stamps, {bad} mismatches"


def check_stamp_x264() -> tuple[bool, str]:
    parts = []
    ok = True
    for crf in (23, 30, 35):
        frac = x264_roundtrip(crf, n_frames=45)
        parts.append(f"crf{crf}={frac:.3f}")
        ok &= frac >= 0.999
    return ok, "x264 stamp decode " + " ".join(parts)


def check_beep() -> tuple[bool, str]:
    track = BeepTrack()
    pcm = np.concatenate([track.frame_samples(i) for i in range(75)])  # 2.5 s
    onsets = detect_beeps(pcm, track.sample_rate)
    ok = len(onsets) == 3 and all(abs(o - k) < 0.006 for k, o in enumerate(onsets))
    peak_db = 20 * np.log10(float(np.abs(pcm).max()))
    return bool(
        ok and abs(peak_db + 12.0) < 0.2
    ), f"onsets {[round(o, 4) for o in onsets]}, peak {peak_db:.2f} dBFS"


def check_scenes_clear_of_stamp() -> tuple[bool, str]:
    sx1, sy1, sx2, sy2 = stamp.STAMP_EXCLUSION
    n = 0
    for name in SCENE_NAMES:
        player = ScenePlayer(f"{name}:4", 30)
        for i in range(0, 120, 3):
            _, _, targets = player.render(i)
            for t in targets:
                n += 1
                x1, y1, x2, y2 = t.quad
                if not (x2 < sx1 or x1 > sx2 or y2 < sy1 or y1 > sy2):
                    return False, f"{name} frame {i}: target {t.quad} touches the stamp region"
    return True, f"{n} target boxes across {len(SCENE_NAMES)} scenes, none touch the stamp region"


def check_metrics() -> tuple[bool, str]:
    lo, hi = metrics.wilson_interval(98, 100)
    r = metrics.frame_recall([(6.0, True, False)] * 98 + [(6.0, True, True)] * 2 + [(1.0, True, True)])
    env = np.full(100, -80.0)
    env[40:60] = -20.0  # 0.4 s .. 0.6 s audible at 10 ms hop
    mc = metrics.mute_coverage([(0.0, 0.3), (0.4, 0.6)], env, 0.01)
    om = metrics.over_mute([(0.4, 0.6)], env, 0.01)
    d = metrics.delay_percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
    ok = (
        abs(lo - 0.9300) < 0.002
        and r.n == 100
        and abs(r.value - 0.98) < 1e-9
        and mc.n_covered == 1
        and abs(mc.coverage - 0.6) < 1e-9
        and om == 0.0
        and d["p50_s"] == 3.0
        and d["max_s"] == 5.0
        and metrics.is_visible_target([(10, 10), (110, 10), (110, 60), (10, 60)])
        and not metrics.is_visible_target([(-90, 10), (10, 10), (10, 60), (-90, 60)])
    )
    return bool(
        ok
    ), f"Wilson(98/100)=({lo:.4f},{hi:.4f}) recall {r.value:.2f} mute coverage {mc.coverage:.2f}"


def check_timing_and_report() -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "timing.jsonl"
        with timing.TimingWriter(p) as w:
            w.header("selftest", "dev", "abc123", "2026-01-01T00:00:00Z", {"cpu": "x"}, 1234)
            for seq in range(10):
                t0 = 100.0 + seq / 30
                w.timing("ingest_decode", seq, seq / 30, t0, t0 + 0.004, "engine")
                w.timing("egress", seq, seq / 30, t0 + 4.9, t0 + 5.0, "engine")
            w.event("hold", 101.0)
        log = timing.read_timing(p)
        delays = timing.end_to_end_delays(log)
        lat = timing.stage_latencies(log)
        timing_ok = (
            len(delays) == 10 and all(abs(d - 5.0) < 1e-9 for d in delays) and lat["egress"]["n"] == 10
        )
        rep = report.Report("selftest", "2026-01-01", {"seed": 1234})
        rep.checks.append(report.Check("delay p95", 5.0, "<=", 5.0, "s"))
        rep.tables.append(report.Table("t", ["a"], [[1.0]]))
        md, js = report.write_report(rep, Path(tmp) / "r")
        first = js.read_bytes()
        report.write_report(rep, Path(tmp) / "r")
        stable = first == js.read_bytes() and json.loads(first)["passed"] is True and md.exists()
    return timing_ok and stable, f"timing delay=5.0 s, {len(lat)} stages; report md+json stable"


def check_dataset_determinism() -> tuple[bool, str]:
    a = dataset.build_dataset(None, 1234, n_per_kind=1)
    b = dataset.build_dataset(None, 1234, n_per_kind=1)
    c = dataset.build_dataset(None, 4321, n_per_kind=1)
    found = specimen.scan_obj(a)
    ok = a["manifest_sha256"] == b["manifest_sha256"] != c["manifest_sha256"] and not found
    return (
        ok,
        f"manifest {a['manifest_sha256'][:12]} (same seed identical, other seed differs); forbidden patterns: {len(found)}",
    )


def check_gpu_lock() -> tuple[bool, str]:
    try:
        with gpu_lock(timeout_s=10.0) as info:
            return True, f"acquired {info.path} after {info.waited_s:.2f} s"
    except GpuLockTimeout as exc:
        return False, str(exc)


CHECKS: list[tuple[str, Check]] = [
    ("verhoeff + SPECIMEN number rules", check_verhoeff),
    ("stamp encode/decode 10k", check_stamp_roundtrip),
    ("stamp through x264 crf 23/30/35", check_stamp_x264),
    ("beep track + onset detector", check_beep),
    ("scenes keep clear of stamp", check_scenes_clear_of_stamp),
    ("metrics on toy data", check_metrics),
    ("timing jsonl + report writer", check_timing_and_report),
    ("dataset determinism + lint", check_dataset_determinism),
    ("gpu lock", check_gpu_lock),
]


def run_selftest(verbose: bool = True) -> bool:
    rows: list[tuple[str, bool, str, float]] = []
    for name, fn in CHECKS:
        t0 = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failed check
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        rows.append((name, ok, detail, (time.perf_counter() - t0) * 1000))
    if verbose:
        width = max(len(r[0]) for r in rows)
        for name, ok, detail, ms in rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {name:<{width}}  {ms:7.0f} ms  {detail}")
        n_ok = sum(1 for r in rows if r[1])
        print(f"selftest: {n_ok}/{len(rows)} passed")
    return all(r[1] for r in rows)
