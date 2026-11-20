"""T2.8 acceptance runner: plate recall/precision and ACCURATE-lane time of ``plates`` on SPECIMEN scenes.

    python -m vision.accept_plates                      # CPU, 60 plate scenes + 40 hard negatives
    python -m vision.accept_plates --plates 80 --json out.json

Scenes come from ``eval.data_gen.video.suite.build_item`` (T2.1): synthetic SPECIMEN vehicles (car, motorbike,
truck rear views, standard and Bharat-series plates, four colour schemes) at clean, light and heavy
augmentation levels, plus ``neg_plate`` (plate-shaped text that is no registration) and ``neg_card`` /
``qr_negative`` scenes as extra negatives. The real OCR (CPU) runs; the detector is driven directly.
The ``heavy`` level (blur up to sigma 3 or 17 px motion, JPEG quality 25-60, brightness 0.35-0.8, glare, up to
40 % occlusion) is unreadable by any OCR; recall is reported per level and for clean+light together.
Recall: a visible plate counts when the union of ``plate`` detections covers >= 70 % of its quad (>= 50 % also
reported). Precision: share of detections whose box overlaps a truth plate. Numbers only: no text is printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from typing import Any

import cv2

from contracts import Category, Config, DetectContext, DetectorConfig, Device, Frame, Lane
from vision.detectors.plates import DETECTOR as PLATES
from vision.resources import VisionResources
from vision.testing_text import coverage, det_quads

from .testing import percentile

NEGATIVE_BUCKETS = ("neg_plate", "neg_card", "qr_negative")


def build_item_bgr(bucket: str, index: int, seed: int) -> tuple[Any, dict[str, Any]]:
    """One SPECIMEN scene (BGR image, truth) from the eval suite (``eval`` is imported lazily)."""
    from eval.data_gen.video.suite import build_item

    img, truth = build_item(bucket, index, seed)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR), truth


def build_scenes(n_plates: int, n_neg: int, seed: int) -> list[dict[str, Any]]:
    scenes: list[dict[str, Any]] = []
    for i in range(n_plates):
        img, truth = build_item_bgr("plate", i, seed)
        scenes.append({"bucket": "plate", "index": i, "image": img, "truth": truth})
    per = max(1, n_neg // len(NEGATIVE_BUCKETS))
    for bucket in NEGATIVE_BUCKETS:
        for i in range(per):
            img, truth = build_item_bgr(bucket, i, seed)
            scenes.append({"bucket": bucket, "index": i, "image": img, "truth": truth})
    return scenes


def _targets(truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in truth["targets"] if t["category"] == "plate" and t["visible"] and not t.get("aux")]


def run(scenes: list[dict[str, Any]], config: Config | None = None, *, warm: int = 3) -> dict[str, Any]:
    import numpy as np

    cfg = config or Config(profile="dev", _env_file=None)
    res = VisionResources(cfg)
    det = PLATES()
    det.configure(DetectorConfig(categories=cfg.categories, device=Device.CPU), res)
    det.warmup()
    ctx = DetectContext(lane=Lane.ACCURATE, categories={}, stride=1)
    for i in range(warm):
        det.detect(Frame(-1 - i, -float(i), 0.0, scenes[i % len(scenes)]["image"]), ctx)
    total_ms: list[float] = []
    own_ms: list[float] = []  # the detector alone (the shared OCR pass already memoised)
    recall: list[bool] = []
    recall50: list[bool] = []
    by_level: dict[str, list[bool]] = defaultdict(list)
    by_series: dict[str, list[bool]] = defaultdict(list)
    by_vehicle: dict[str, list[bool]] = defaultdict(list)
    by_layout: dict[str, list[bool]] = defaultdict(list)
    missed: list[str] = []
    dets_n = fp_dets = 0
    neg_frames = neg_fp_frames = 0
    neg_by_bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for seq, sc in enumerate(scenes):
        frame = Frame(seq, seq / 4.0, 0.0, sc["image"])
        t0 = time.perf_counter()
        dets = det.detect(frame, ctx)  # runs the shared OCR pass (memoised for the next call)
        total_ms.append((time.perf_counter() - t0) * 1000.0)
        t1 = time.perf_counter()
        det.detect(frame, ctx)
        own_ms.append((time.perf_counter() - t1) * 1000.0)
        quads = det_quads(dets, Category.PLATE)
        truths = _targets(sc["truth"])
        for t in truths:
            tq = np.array(t["quad"], np.float32)
            cov = coverage(tq, quads)
            recall.append(cov >= 0.7)
            recall50.append(cov >= 0.5)
            by_level[sc["truth"]["level"]].append(cov >= 0.7)
            by_series[str(t.get("series"))].append(cov >= 0.7)
            by_vehicle[str(t.get("vehicle"))].append(cov >= 0.7)
            by_layout[str(t.get("layout"))].append(cov >= 0.7)
            if cov < 0.7:
                missed.append(
                    f"plate{sc['index']}:{t.get('vehicle')}/{t.get('layout')}/{sc['truth']['level']}"
                )
        if sc["bucket"] == "plate":
            dets_n += len(dets)
            for q in quads:
                if not any(coverage(np.array(t["quad"], np.float32), [q]) >= 0.3 for t in truths):
                    fp_dets += 1
        else:
            neg_frames += 1
            neg_by_bucket[sc["bucket"]][0] += 1
            if dets:
                neg_fp_frames += 1
                neg_by_bucket[sc["bucket"]][1] += 1
    res.close()

    def rate(vs: list[bool]) -> float:
        return round(sum(vs) / max(1, len(vs)), 3)

    return {
        "images": len(scenes),
        "plate_targets": len(recall),
        "plate_recall": rate(recall),
        "plate_recall_cov50": rate(recall50),
        "plate_recall_clean_light": rate(by_level["clean"] + by_level["light"]),
        "recall_by_level": {k: rate(v) for k, v in sorted(by_level.items())},
        "recall_by_series": {k: rate(v) for k, v in sorted(by_series.items())},
        "recall_by_vehicle": {k: rate(v) for k, v in sorted(by_vehicle.items())},
        "recall_by_layout": {k: rate(v) for k, v in sorted(by_layout.items())},
        "missed": missed,
        "detections_on_plate_scenes": dets_n,
        "false_detections_on_plate_scenes": fp_dets,
        "precision": round(1.0 - fp_dets / max(1, dets_n), 3),
        "negative_frames": neg_frames,
        "negative_frames_with_a_plate_mask": neg_fp_frames,
        "negative_by_bucket": {k: {"frames": v[0], "false": v[1]} for k, v in neg_by_bucket.items()},
        "detector_only_ms_median": round(statistics.median(own_ms), 2),
        "detector_only_ms_p95": round(percentile(own_ms, 95), 2),
        "ms_median": round(statistics.median(total_ms), 1),
        "ms_p95": round(percentile(total_ms, 95), 1),
        "ms_max": round(max(total_ms), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plates", type=int, default=60)
    ap.add_argument("--negatives", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    report = run(build_scenes(args.plates, args.negatives, args.seed))
    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
