"""``python -m eval.data_gen.audio``: generate (or verify) the spoken-PII audio suite.

Exit codes: 0 ok, 1 a forbidden (possibly real) pattern was found in the output, 2 environment problem.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..common.specimen import scan_obj
from .suite import DEFAULT_N, DEFAULT_SEED, build_suite, manifest_hash

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = "eval/data/generated/audio"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.data_gen.audio", description=__doc__)
    p.add_argument(
        "--out", default=DEFAULT_OUT, help="output directory (relative paths resolve from the repo root)"
    )
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--n", type=int, default=DEFAULT_N, help="clips (default %(default)s)")
    p.add_argument("--engine", choices=("auto", "flite", "tone"), default="auto")
    a = p.parse_args(argv)
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    t0 = time.time()
    try:
        m = build_suite(out, a.seed, a.n, a.engine)
    except (RuntimeError, OSError) as exc:
        print(f"ENV ERROR: {exc}", file=sys.stderr)
        return 2
    bad = scan_obj(m)
    print(
        json.dumps(
            {
                "out": str(out),
                "engine": m["engine"],
                "totals": m["totals"],
                "seconds": round(time.time() - t0, 1),
                "manifest_sha256": manifest_hash(m),
            },
            indent=1,
        )
    )
    if bad:
        print(f"FORBIDDEN PATTERNS in output: {len(bad)}", file=sys.stderr)
        return 1
    return 0
