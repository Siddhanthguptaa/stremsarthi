"""T1.3 acceptance runner: SPECIMEN card recall/precision and ACCURATE-lane latency through ``LaneHost``.

    python -m vision.accept_cards                       # CPU, 72 positives + 32 negatives
    python -m vision.accept_cards --per-kind 54 --seed 2 --json out.json
    flock /tmp/streamsarthi-gpu.lock python -m vision.accept_cards --provider cuda   # GPU (+ nvidia-smi VRAM)

Every scene is a synthetic SPECIMEN card (or a negative frame) from ``vision.testing`` placed on a desk with
tilt, distance, blur, glare and JPEG artefacts. Frames go through ``vision.host.build`` (the planner is the
assembler, the OCR is the real CPU or CUDA service); sample i carries ``pts = 10 * i`` so the 2 s card hold
of one sample never leaks into the next. Recall counts a card as found when the masks overlap it (IoU >= 0.5)
and as covered when they cover >= 90 % of its area. Latency is the ACCURATE lane service time per frame.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from typing import Any

from contracts import Config, Lane, LaneResult
from contracts.fakes import InProcessBus

from . import host as vhost
from .testing import Scene, augmented_set, percentile, score_scene


def gpu_memory_mb() -> int | None:
    """Total used GPU memory in MiB from ``nvidia-smi`` (None when unavailable)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def make_config(provider: str, mem_limit_mb: int = 800) -> Config:
    if provider == "cuda":
        return Config(
            profile="dev",
            _env_file=None,
            gpu={"enabled": True, "ort_provider": "cuda", "mem_limit_mb": mem_limit_mb},
        )  # type: ignore[arg-type]
    return Config(profile="dev", _env_file=None)


def _wait_for(host: vhost.VisionHost, seq: int, timeout: float = 20.0) -> LaneResult:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for r in host.poll():
            if r.lane is Lane.ACCURATE and r.frame_seq == seq:
                return r
        time.sleep(0.005)
    raise TimeoutError(f"no ACCURATE result for frame {seq}")


def run(
    scenes: list[Scene], provider: str = "cpu", *, mem_limit_mb: int = 800, warm_frames: int = 3
) -> dict[str, Any]:
    cfg = make_config(provider, mem_limit_mb)
    baseline = gpu_memory_mb() if provider == "cuda" else None
    host = vhost.build(cfg, InProcessBus())
    host.start()  # loads the OCR sessions (warmup)
    after_load = gpu_memory_mb() if provider == "cuda" else None
    peak = after_load
    results: list[tuple[Scene, LaneResult]] = []
    try:
        for i in range(warm_frames):  # steady-state timing: ORT first runs allocate
            host.submit(scenes[i % len(scenes)].frame(1_000_000 + i, -100.0 * (i + 1)))
            _wait_for(host, 1_000_000 + i)
        host.poll()
        skip = len(host.stats["accurate"]["service_seconds"])
        for i, sc in enumerate(scenes):
            host.submit(sc.frame(i, 10.0 * i))
            results.append((sc, _wait_for(host, i)))
            if provider == "cuda":
                used = gpu_memory_mb()
                if used is not None:
                    peak = max(peak or 0, used)
        service = list(host.stats["accurate"]["service_seconds"])[skip:]
        failed = host.stats["accurate"]["failed"]
    finally:
        host.close()
    scores = [(sc, score_scene(sc, r)) for sc, r in results]
    pos = [s for sc, s in scores if sc.positive]
    neg = [s for sc, s in scores if not sc.positive]
    tp = sum(1 for s in pos if s.masked)
    covered = sum(1 for s in pos if s.covered)
    fp = sum(1 for s in neg if s.masked)
    ms = [t * 1000.0 for t in service]
    by_kind: dict[str, dict[str, int]] = {}
    for sc, s in scores:
        k = by_kind.setdefault(sc.kind, {"n": 0, "masked": 0, "covered": 0})
        k["n"] += 1
        k["masked"] += int(s.masked)
        k["covered"] += int(s.covered)
    out: dict[str, Any] = {
        "provider": provider,
        "positives": len(pos),
        "negatives": len(neg),
        "recall_masked": tp / max(1, len(pos)),
        "recall_covered_90": covered / max(1, len(pos)),
        "false_positive_frames": fp,
        "precision": tp / max(1, tp + fp),
        "lane_failures": failed,
        "latency_ms": {
            "p50": round(statistics.median(ms), 1),
            "p95": round(percentile(ms, 95), 1),
            "max": round(max(ms), 1),
            "n": len(ms),
        },
        "by_kind": by_kind,
        "misses": [
            {"kind": sc.kind, "params": sc.params, "coverage": round(s.coverage, 2)}
            for sc, s in scores
            if sc.positive and not (s.masked and s.covered)
        ],
    }
    if provider == "cuda":
        out["vram_mb"] = {"baseline": baseline, "after_load": after_load, "peak": peak}
        if baseline is not None and peak is not None:
            out["vram_mb"]["delta_peak"] = peak - baseline
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--per-kind", type=int, default=36, help="positives per card kind (x2 kinds)")
    ap.add_argument("--negatives", type=int, default=8, help="negatives per negative kind (x4 kinds)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mem-limit-mb", type=int, default=800)
    ap.add_argument("--json", help="write the result to this file")
    args = ap.parse_args(argv)
    scenes = augmented_set(args.per_kind, args.negatives, args.seed)
    result = run(scenes, args.provider, mem_limit_mb=args.mem_limit_mb)
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0 if result["recall_masked"] >= 0.95 and result["lane_failures"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
