"""T2.7 acceptance runner: SPECIMEN QR scenes through ``VisionHost`` (FAST lane, planner, tracker), CPU only.

Scoring per frame (``frame`` = the scene shown to the host once, as a fresh track):
- positive (payment QR visible): masked if a ``UPI_QR`` request covers at least ``COVER`` of the code's quad
  (symbol plus quiet zone) -> recall;
- non-payment QR and QR-free frames: any ``UPI_QR`` request is a false positive.

``python -m vision.detectors.upi_qr.accept [--json out.json] [--positives N]`` prints a report.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

import cv2
import numpy as np

from contracts import Category, Config, Frame, Lane, LaneResult
from contracts.fakes import InProcessBus

from .testing import QrScene, augmented_set

COVER = 0.90


def coverage(mask_boxes: list[tuple[float, float, float, float]], quad: np.ndarray) -> float:
    """Fraction of the truth quad's area covered by the union of axis-aligned mask boxes."""
    x1, y1 = np.floor(quad.min(axis=0)).astype(int)
    x2, y2 = np.ceil(quad.max(axis=0)).astype(int)
    x1, y1 = max(x1, 0), max(y1, 0)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    truth = np.zeros((y2 - y1, x2 - x1), np.uint8)
    cv2.fillConvexPoly(truth, np.round(quad - [x1, y1]).astype(np.int32), 1)
    mask = np.zeros_like(truth)
    for bx1, by1, bx2, by2 in mask_boxes:
        a, b = int(max(bx1, x1) - x1), int(max(by1, y1) - y1)
        c, d = int(min(bx2, x2) - x1), int(min(by2, y2) - y1)
        if c > a and d > b:
            mask[b:d, a:c] = 1
    area = int(truth.sum())
    return float((truth & mask).sum()) / area if area else 0.0


def _mask_boxes(result: LaneResult) -> list[tuple[float, float, float, float]]:
    return [q.bbox() for r in result.requests if r.category is Category.UPI_QR for q in r.quads]


def run(scenes: list[QrScene], *, config: Config | None = None, registry: Any = None) -> dict[str, Any]:
    """Drive every scene through a fresh ``VisionHost`` (one host per scene so tracks never carry over)."""
    from vision import host as vhost
    from vision.detectors.upi_qr import DETECTOR
    from vision.planner import planner_factory
    from vision.resources import VisionResources

    cfg = config or Config(profile="dev", _env_file=None)
    reg = registry or {"upi_qr": DETECTOR}
    pos_total = pos_hit = pos_cov = 0
    other_total = other_fp = none_total = none_fp = 0
    scan_ms: list[float] = []
    lane_ms: list[float] = []
    misses: list[dict[str, Any]] = []
    fps: list[dict[str, Any]] = []
    failures = 0
    for i, sc in enumerate(scenes):
        host = vhost.VisionHost(
            cfg,
            InProcessBus(),
            registry=reg,
            resources=VisionResources(cfg),
            assembler_factory=planner_factory,
        )
        host.start()
        try:
            t0 = time.perf_counter()
            host.submit(Frame(0, 0.0, 0.0, sc.image))
            res: LaneResult | None = None
            end = time.monotonic() + 10.0
            while res is None and time.monotonic() < end:
                for r in host.poll():
                    if r.lane is Lane.FAST:
                        res = r
                time.sleep(0.002)
            lane_ms.append((time.perf_counter() - t0) * 1000.0)
            if res is None or not res.ok:
                failures += 1
                continue
            det = host.detectors["upi_qr"]
            scan_ms.append(float(getattr(det, "last_ms", 0.0)))
        finally:
            host.close()
        boxes = _mask_boxes(res)
        if sc.kind == "upi":
            pos_total += 1
            cov = coverage(boxes, sc.quad) if sc.quad is not None else 0.0
            pos_cov += cov >= COVER
            if boxes and cov > 0:
                pos_hit += 1
            if cov < COVER:
                misses.append({"i": i, "coverage": round(cov, 2), **sc.params})
        elif sc.kind == "other":
            other_total += 1
            if boxes:
                other_fp += 1
                fps.append({"i": i, "kind": "other", **sc.params})
        else:
            none_total += 1
            if boxes:
                none_fp += 1
                fps.append({"i": i, "kind": "none"})

    def pct(a: list[float], q: float) -> float:
        return round(float(np.percentile(a, q)), 2) if a else 0.0

    masked_frames = pos_cov + other_fp + none_fp
    return {
        "positives": pos_total,
        "recall_covered": round(pos_cov / pos_total, 4) if pos_total else 0.0,
        "recall_any_mask": round(pos_hit / pos_total, 4) if pos_total else 0.0,
        "precision": round(pos_cov / masked_frames, 4) if masked_frames else 1.0,
        "non_payment_qr": other_total,
        "non_payment_fp": other_fp,
        "qr_free_frames": none_total,
        "qr_free_fp": none_fp,
        "lane_failures": failures,
        "scan_ms": {"p50": pct(scan_ms, 50), "p95": pct(scan_ms, 95), "max": pct(scan_ms, 100)},
        "scan_ms_mean": round(statistics.fmean(scan_ms), 2) if scan_ms else 0.0,
        "lane_ms": {"p50": pct(lane_ms, 50), "p95": pct(lane_ms, 95)},
        "misses": misses,
        "false_positives": fps,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--positives", type=int, default=72)
    ap.add_argument("--other", type=int, default=24)
    ap.add_argument("--none", type=int, default=24)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    scenes = augmented_set(args.positives, args.other, args.none, args.seed)
    result = run(scenes)
    print(json.dumps(result, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)


if __name__ == "__main__":
    main()
