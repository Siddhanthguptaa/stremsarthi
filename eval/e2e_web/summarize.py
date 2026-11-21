"""Summarise a Playwright JSON report and the axe JSON-lines file for the gate (T6.5).

    python -m eval.e2e_web.summarize --report /tmp/ss-e2e-report.json --axe /tmp/ss-e2e-axe.jsonl --out summary.md

Exit 0 when every test passed, 1 when any failed, 2 when the report is missing or has no tests (nothing ran).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def walk(suite: dict[str, Any], trail: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    here = trail + ((suite.get("title") or "",) if suite.get("title") else ())
    for spec in suite.get("specs", []):
        for t in spec.get("tests", []):
            res = t.get("results", [{}])[-1]
            out.append({
                "title": " > ".join((*here, spec["title"])), "status": t.get("status"), "project": t.get("projectName"),
                "error": ((res.get("error") or {}).get("message") or "").splitlines()[:3],
                "ms": res.get("duration", 0),
            })  # fmt: skip
    for child in suite.get("suites", []):
        out.extend(walk(child, here))
    return out


def load_axe(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for r in rows:
        k = (r["route"], r["viewport"], r["id"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--axe", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    rp = Path(a.report)
    if not rp.exists():
        print("no Playwright report: nothing ran", file=sys.stderr)
        return 2
    data = json.loads(rp.read_text())
    tests: list[dict[str, Any]] = []
    for s in data.get("suites", []):
        tests.extend(walk(s))
    if not tests:
        print("Playwright report has no tests", file=sys.stderr)
        return 2
    counts = Counter(t["status"] for t in tests)
    axe = load_axe(Path(a.axe))
    by_impact: Counter[str] = Counter(r["impact"] for r in axe)
    lines = [
        "# Phase 6 browser tests",
        "",
        f"Tests: {len(tests)}; " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
    ]
    bad = [t for t in tests if t["status"] not in ("expected", "skipped")]
    if bad:
        lines += ["## Failed", ""]
        for t in bad:
            lines.append(
                f"- {t['title']} [{t['project']}]: " + " | ".join(t["error"]).replace("\n", " ")[:300]
            )
        lines.append("")
    lines += [
        "## axe violations (all impacts, de-duplicated by route, viewport and rule)",
        "",
        "impact counts: " + (", ".join(f"{k} {v}" for k, v in sorted(by_impact.items())) or "none"),
        "",
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in axe:
        grouped[r["route"]].append(r)
    for route, rows in sorted(grouped.items()):
        lines.append(f"### {route}")
        for r in rows:
            lines.append(
                f"- [{r['impact']}] {r['id']} at {r['viewport']}: {r['help']}; " + "; ".join(r["targets"][:3])
            )
    Path(a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
