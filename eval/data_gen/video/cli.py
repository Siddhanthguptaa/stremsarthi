"""CLI for the video suite: ``python -m eval.data_gen.video`` (also wired as ``eval.run gen-video``).

Exit codes: 0 ok, 1 forbidden pattern found in the output, 2 environment problem (fonts, raqm, portraits).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from ..common.specimen import scan_obj
from .suite import BUCKETS, DEFAULT_FACES_CACHE, DEFAULT_N, EnvError, build_suite

EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = "eval/data/generated/video"


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--out", default=DEFAULT_OUT, help="output directory (relative paths resolve from the repo root)"
    )
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--n", type=int, default=DEFAULT_N, help="images per bucket (default %(default)s)")
    p.add_argument("--buckets", nargs="*", default=None, help=f"subset of: {' '.join(BUCKETS)}")
    p.add_argument("--workers", type=int, default=None, help="process count (default min(6, cpus))")
    p.add_argument(
        "--faces-cache", default=DEFAULT_FACES_CACHE, help="where the public-domain portraits are cached"
    )
    p.add_argument(
        "--clips",
        type=int,
        default=2,
        help="30 fps clips per clip bucket (0 = images only, default %(default)s)",
    )
    p.add_argument("--clip-seconds", type=float, default=5.0, help="clip length (spec: 5 to 10 s)")
    p.add_argument("--no-fetch", action="store_true", help="do not download missing portraits")


def run(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    cache = a.faces_cache if Path(a.faces_cache).is_absolute() else str(ROOT / a.faces_cache)
    try:
        manifest = build_suite(
            out,
            a.seed,
            a.n,
            tuple(a.buckets) if a.buckets else None,
            a.workers,
            cache,
            fetch=not a.no_fetch,
            clips_per_bucket=a.clips,
            clip_seconds=a.clip_seconds,
        )
    except EnvError as exc:
        print(f"ENV ERROR: {exc}", file=sys.stderr)
        return EXIT_ENV
    counts = Counter(i["bucket"] for i in manifest["items"])
    size_mb = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    n_targets = sum(len(i["truth"]["targets"]) for i in manifest["items"])
    n_dis = sum(len(i["truth"]["distractors"]) for i in manifest["items"])
    print(
        f"wrote {len(manifest['items'])} images in {len(counts)} buckets to {out} "
        f"({size_mb:.0f} MB, {manifest['_seconds']} s) manifest_sha256={manifest['manifest_sha256'][:16]}"
    )
    print(f"ground truth: {n_targets} target quads, {n_dis} distractor quads")
    for b, n in counts.items():
        print(f"  {b:22s} {n}")
    if "clips" in manifest:
        ci = manifest["clips"]["items"]
        print(
            f"clips: {len(ci)} x {manifest['clips']['seconds']} s at {manifest['clips']['fps']} fps, "
            f"{sum(c['visible_target_frames'] for c in ci)} visible target-frames"
        )
    if "enroll_sets" in manifest:
        print(
            "  enrolment frames: "
            + ", ".join(f"{k}={len(v['frames'])}" for k, v in manifest["enroll_sets"].items())
        )
    bad = scan_obj({k: v for k, v in manifest.items() if not k.startswith("_")})
    if bad:
        print(f"FORBIDDEN PATTERNS FOUND: {bad[:5]}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.data_gen.video", description=__doc__)
    add_arguments(p)
    return run(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
