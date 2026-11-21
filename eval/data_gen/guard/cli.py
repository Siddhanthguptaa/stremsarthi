"""``python -m eval.data_gen.guard``: generate the guard eval set. Exit 0 ok, 1 forbidden pattern in the manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..common.specimen import scan_obj
from .suite import DEFAULT_SEED, build_suite, manifest_hash

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = "eval/data/generated/guard"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.data_gen.guard", description=__doc__)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--quick", action="store_true", help="a quarter-size set (unit tests)")
    a = p.parse_args(argv)
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    m = build_suite(out, a.seed, quick=a.quick)
    print(
        json.dumps(
            {
                "out": str(out),
                "totals": m["totals"],
                "tts_engine": m["tts_engine"],
                "seconds": m["gen_seconds"],
                "manifest_sha256": manifest_hash(m),
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    bad = scan_obj(m)
    if bad:
        print(f"FORBIDDEN PATTERNS in output: {len(bad)}", file=sys.stderr)
        return 1
    return 0
