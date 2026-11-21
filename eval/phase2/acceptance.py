"""Gate 2 acceptance: does the gate fail on a seeded defect and pass the same rows on the clean build?

``compare`` reads the ``offline.json`` of a clean run and of a run with one seeded defect (``seeded.py``) over the same
subset and says, per defect kind, which checks must flip. Used by ``scripts/gate_phase2_acceptance.sh``.

* ``blind:D``   the rows of detector D must FAIL with recall far below the clean run (silent regression)
* ``crash:D``   "detector exceptions during the run" must FAIL (and the clean run has none)
* ``absent:D``  the rows of D must be NOT RUN and "all expected detectors loaded and ran" must FAIL
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import seeded
from .rows import ROWS


def _checks(leg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in leg["checks"]}


def _status(c: dict[str, Any]) -> str:
    return "NOT RUN" if c.get("not_run") else ("PASS" if c["passed"] else "FAIL")


def rows_of(detector: str) -> list[str]:
    return [f"{r.title} recall [stills]" for r in ROWS if r.detector == detector]


def compare(
    clean: dict[str, Any], defect: dict[str, Any], spec: str, subset_rows: list[str] | None = None
) -> list[str]:
    """Problems found (empty = the gate behaved as it must)."""
    kind, ident = seeded.parse(spec)
    cc, dc = _checks(clean), _checks(defect)
    names = [n for n in rows_of(ident) if n in dc and (subset_rows is None or n in subset_rows)]
    # rows the clean subset run has a number for (a thin subset leaves other rows of the detector NOT RUN in both runs)
    scored = [n for n in names if n in cc and not cc[n].get("not_run") and cc[n]["value"] is not None]
    problems: list[str] = []
    if defect.get("passed"):
        problems.append("the defective build passed the leg")
    if not scored:
        problems.append(f"the clean run has no scored row of detector '{ident}' (subset too thin?)")
    for n in scored:
        d, c = dc[n], cc[n]
        if kind == "blind":
            if d.get("not_run") or d["passed"] or d["value"] is None or d["value"] > 0.1:
                problems.append(f"blind defect not caught on '{n}': {d['value']} (clean {c['value']:.3f})")
        elif not d.get("not_run"):  # absent package, or every call raised (the detector is then marked error)
            problems.append(f"row '{n}' of the {kind} detector is not NOT RUN")
    if kind == "crash":
        d = dc.get("detector exceptions during the run")
        if d is None or d["passed"] or not d["value"]:
            problems.append("crash defect did not fail 'detector exceptions during the run'")
        if cc.get("detector exceptions during the run", {}).get("value"):
            problems.append("the clean run already had detector exceptions")
    if kind == "absent":
        d = dc.get("all expected detectors loaded and ran")
        if d is None or d["passed"]:
            problems.append("absent defect did not fail 'all expected detectors loaded and ran'")
    return problems


def main(argv: list[str]) -> int:
    clean_p, defect_p, spec = argv
    clean = json.loads(Path(clean_p).read_text(encoding="utf-8"))
    defect = json.loads(Path(defect_p).read_text(encoding="utf-8"))
    problems = compare(clean, defect, spec)
    cc, dc = _checks(clean), _checks(defect)
    kind, ident = seeded.parse(spec)
    for n in rows_of(ident):
        if n in dc and n in cc and not cc[n].get("not_run"):
            c, d = cc[n], dc[n]
            print(f"  {n}: clean {_status(c)} {c['value']} -> defect {_status(d)} {d['value']}")
    for p in problems:
        print(f"  PROBLEM: {p}")
    print(f"  {spec}: {'gate caught the defect' if not problems else 'GATE DID NOT BEHAVE'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
