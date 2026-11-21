"""``python -m eval.data_gen.chat``: generate the chat eval set. Exit 0 ok, 1 forbidden pattern in the output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..common.specimen import scan_obj
from .suite import DEFAULT_SEED, build_suite, manifest_hash

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = "eval/data/generated/chat"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.data_gen.chat", description=__doc__)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = p.parse_args(argv)
    out = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    m = build_suite(out, a.seed)
    print(
        json.dumps(
            {"out": str(out), "totals": m["totals"], "manifest_sha256": manifest_hash(m)},
            indent=1,
            ensure_ascii=False,
        )
    )
    bad = scan_obj(m)
    if bad:
        print(f"FORBIDDEN PATTERNS in output: {len(bad)}", file=sys.stderr)
        return 1
    return 0
