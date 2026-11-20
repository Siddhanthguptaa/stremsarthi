"""Latency probe: times every model that is on disk, on a synthetic 1280x720 frame.

    python -m vision.probe                       # CPU, prints a table
    python -m vision.probe --provider cuda       # GPU: run under `flock /tmp/streamsarthi-gpu.lock`
    python -m vision.probe --json out.json --runs 30

What is timed (milliseconds, p50/p95/mean over ``--runs`` after ``--warmup`` runs):
- ``yunet``       resize 1280x720 -> 640x360 plus ``cv2.FaceDetectorYN.detect`` (the FAST-lane work)
- ``sface``       one 112x112 face embedding (``cv2.FaceRecognizerSF.feature``)
- ``det_ort``     bare ORT run of a PP-OCR detector at the static shape 1x3x544x960
- ``det_full``    RapidOCR ``TextDetector`` end to end (resize, normalise, ORT run, DB post-process)
- ``rec``         one batch of ``ocr.rec_max_crops`` (8) crops, 3x48x320, per recogniser

The frame is synthetic (Latin text lines on a gradient, no faces, SPECIMEN-free), so face detection is the
no-face case and ``det_full`` has a realistic number of boxes. Nothing here reads real images.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from models.prepare import DEFAULT_CACHE, artifact_path, load_manifest, sha256_file

from .ort import OrtSessionFactory, OrtSettings

DET_SHAPE = (544, 960)
YUNET_SIZE = (640, 360)
REC_SHAPE = (48, 320)
REC_BATCH = 8
GPU_PROBE_MEM_MB = 800  # the dev-profile share for vision (docs/specs/vision.md section 11)


def synthetic_frame(seed: int = 7) -> np.ndarray:
    """1280x720 BGR: gradient, filled panels and ~24 lines of Latin text (no faces, no personal data)."""
    rng = np.random.default_rng(seed)
    x = np.linspace(40, 200, 1280, dtype=np.float32)
    y = np.linspace(0, 40, 720, dtype=np.float32)[:, None]
    base = np.clip(x[None, :] + y, 0, 255).astype(np.uint8)
    img = cv2.merge([base, (base * 0.8).astype(np.uint8), (base * 0.6).astype(np.uint8)])
    for _ in range(6):
        x0, y0 = int(rng.integers(0, 1000)), int(rng.integers(0, 560))
        cv2.rectangle(
            img, (x0, y0), (x0 + 240, y0 + 140), tuple(int(v) for v in rng.integers(30, 220, 3)), -1
        )
    words = ["SPECIMEN", "Order", "Total", "Invoice", "Sample", "Demo", "Name", "Status", "Ready", "Open"]
    for row in range(24):
        text = (
            " ".join(words[int(i)] for i in rng.integers(0, len(words), 4))
            + f" {int(rng.integers(100, 999))}"
        )
        org = (int(rng.integers(10, 500)), 30 + row * 29)
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.8, (245, 245, 245), 2, cv2.LINE_AA)
    return img


def time_ms(fn: Callable[[], Any], runs: int, warmup: int) -> dict[str, float]:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p95 = samples[min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))]
    return {
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(p95, 2),
        "mean_ms": round(statistics.fmean(samples), 2),
        "runs": runs,
    }


def _present(name: str, cache: Path) -> Path | None:
    """Path of a manifest artifact if it is on disk and hash-correct; else None."""
    try:
        path = artifact_path(name, cache)
    except KeyError:
        return None
    if not path.is_file():
        return None
    if sha256_file(path) != load_manifest()["artifacts"][name]["sha256"]:
        raise ValueError(f"SHA-256 mismatch for {path}; re-run models/prepare.py")
    return path


def probe_yunet(path: Path, frame: np.ndarray, runs: int, warmup: int) -> dict[str, Any]:
    det = cv2.FaceDetectorYN.create(str(path), "", YUNET_SIZE, 0.6, 0.3, 50)

    def run() -> None:
        small = cv2.resize(frame, YUNET_SIZE, interpolation=cv2.INTER_AREA)
        det.detect(small)

    return {
        "input": f"{YUNET_SIZE[0]}x{YUNET_SIZE[1]}",
        "device": "cpu (cv2 dnn)",
        **time_ms(run, runs, warmup),
    }


def probe_sface(path: Path, runs: int, warmup: int) -> dict[str, Any]:
    rec = cv2.FaceRecognizerSF.create(str(path), "")
    face = np.random.default_rng(1).integers(0, 255, (112, 112, 3), dtype=np.uint8)
    return {"input": "112x112", "device": "cpu (cv2 dnn)", **time_ms(lambda: rec.feature(face), runs, warmup)}


def _det_tensor(frame: np.ndarray, mean: list[float], std: list[float]) -> np.ndarray:
    h, w = DET_SHAPE
    img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    img = (img - np.array(mean, np.float32)) / np.array(std, np.float32)
    return np.ascontiguousarray(img.transpose(2, 0, 1)[None])


def probe_det(
    name: str, frame: np.ndarray, factory: OrtSessionFactory, runs: int, warmup: int
) -> dict[str, Any]:
    art = load_manifest()["artifacts"][name]
    model_id = f"{name}:{DET_SHAPE[0]}x{DET_SHAPE[1]}"
    sess = factory.session(model_id)
    meta = factory.metadata[model_id]
    inp = sess.get_inputs()[0].name
    tensor = _det_tensor(frame, art["preprocess"]["mean"], art["preprocess"]["std"])
    out: dict[str, Any] = {
        "input": f"{DET_SHAPE[1]}x{DET_SHAPE[0]}",
        "device": meta["execution_provider"],
        "ort_only": time_ms(lambda: sess.run(None, {inp: tensor}), runs, warmup),
    }
    try:
        from omegaconf import OmegaConf
        from rapidocr.ch_ppocr_det import TextDetector
        from rapidocr.utils.typings import EngineType

        post = art.get("upstream_postprocess", {})
        cfg = OmegaConf.create(
            {
                "limit_side_len": DET_SHAPE[1],
                "limit_type": "max",
                "mean": art["preprocess"]["mean"],
                "std": art["preprocess"]["std"],
                "thresh": 0.3,
                "box_thresh": 0.5,
                "max_candidates": post.get("max_candidates", 1000),
                "unclip_ratio": 1.6,
                "use_dilation": True,
                "score_mode": "fast",
            },
            flags={"allow_objects": True},
        )
        cfg.engine_type = EngineType.ONNXRUNTIME
        cfg.session = sess  # a verified session bypasses RapidOCR's download resolver
        detector = TextDetector(cfg)
        boxes = detector(frame).boxes
        out["boxes_on_probe_frame"] = 0 if boxes is None else len(boxes)
        out["full"] = time_ms(lambda: detector(frame), runs, warmup)
    except Exception as exc:  # noqa: BLE001 - the probe reports, it does not crash
        out["full_error"] = f"{type(exc).__name__}: {exc}"
    return out


def probe_rec(name: str, factory: OrtSessionFactory, runs: int, warmup: int) -> dict[str, Any]:
    sess = factory.session(name)
    meta = factory.metadata[name]
    shape = sess.get_inputs()[0].shape
    inp = sess.get_inputs()[0].name
    batch = np.random.default_rng(3).random((REC_BATCH, 3, *REC_SHAPE), dtype=np.float32) * 2 - 1
    return {
        "input": f"{REC_BATCH}x3x{REC_SHAPE[0]}x{REC_SHAPE[1]} (model declares {shape})",
        "device": meta["execution_provider"],
        **time_ms(lambda: sess.run(None, {inp: batch}), runs, warmup),
    }


def run_probe(provider: str, runs: int, warmup: int, cache: Path, only: set[str] | None) -> dict[str, Any]:
    frame = synthetic_frame()
    settings = OrtSettings(provider, threads=2, mem_limit_mb=GPU_PROBE_MEM_MB if provider == "cuda" else 0)  # type: ignore[arg-type]
    factory = OrtSessionFactory(settings, cache)
    results: dict[str, Any] = {
        "provider": provider,
        "frame": "1280x720 synthetic",
        "ort_threads": settings.threads,
    }
    skipped: list[str] = []

    def want(key: str) -> bool:
        return only is None or key in only

    if provider == "cpu":  # cv2 face models run on the CPU only
        for key, fn in (
            ("yunet-2023mar", lambda p: probe_yunet(p, frame, runs, warmup)),
            ("sface-2021dec", lambda p: probe_sface(p, runs, warmup)),
        ):
            path = _present(key, cache)
            if path is None:
                skipped.append(key)
            elif want(key):
                results[key] = fn(path)
    for key in ("ppocr-v6-tiny", "ppocr-v5-mobile", "ppocr-v6-small"):
        if _present(key, cache) is None:
            skipped.append(key)
        elif want(key):
            results[key] = probe_det(key, frame, factory, runs, warmup)
    for key in (
        "ppocr-v5-english-rec",
        "ppocr-v5-devanagari-rec",
        "ppocr-v5-tamil-rec",
        "ppocr-v5-telugu-rec",
    ):
        if _present(key, cache) is None:
            skipped.append(key)
        elif want(key):
            results[key] = probe_rec(key, factory, runs, warmup)
    results["skipped_not_on_disk"] = skipped
    return results


def _print(results: dict[str, Any]) -> None:
    print(f"provider={results['provider']} frame={results['frame']} ort_threads={results['ort_threads']}")
    print(f"{'model':26s} {'stage':9s} {'p50 ms':>8s} {'p95 ms':>8s} {'mean ms':>8s}  input / device")
    for key, val in results.items():
        if not isinstance(val, dict) or "input" not in val:
            continue
        if "ort_only" in val:
            for stage in ("ort_only", "full"):
                if stage in val:
                    t = val[stage]
                    print(
                        f"{key:26s} {stage:9s} {t['p50_ms']:8.1f} {t['p95_ms']:8.1f} {t['mean_ms']:8.1f}  {val['input']} / {val['device']}"
                    )
            if "full_error" in val:
                print(f"{key:26s} full      error: {val['full_error']}")
        else:
            print(
                f"{key:26s} {'run':9s} {val['p50_ms']:8.1f} {val['p95_ms']:8.1f} {val['mean_ms']:8.1f}  {val['input']} / {val['device']}"
            )
    if results["skipped_not_on_disk"]:
        print("skipped (not on disk):", ", ".join(results["skipped_not_on_disk"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--provider", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--only", nargs="*", help="Manifest ids to probe (default: all on disk)")
    parser.add_argument("--json", type=Path, help="Also write the numbers to this file")
    args = parser.parse_args(argv)
    results = run_probe(
        args.provider, args.runs, args.warmup, args.cache_dir, set(args.only) if args.only else None
    )
    _print(results)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
