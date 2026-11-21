"""Seeded defects for the Gate 2 acceptance run: a deliberately broken detector build that the gate must fail.

``--seeded-defect KIND:DETECTOR`` (offline leg) breaks one loaded detector after it was configured:

* ``blind:cards``   the detector loads and runs but never reports anything (a silent regression: every card is a miss)
* ``crash:text``    every ``detect`` call raises (the engine would slate those frames; the gate counts the exceptions)
* ``absent:plates`` the detector package is gone (its rows are NOT RUN and the summary check fails)

Nothing here touches ``vision/``: the instances are patched in memory. ``scripts/gate_phase2_acceptance.sh`` runs a
small clean build and a defective build side by side and checks that the defect flips exactly the rows it should.
"""

from __future__ import annotations

from typing import Any

from .detectors import MISSING, DetectorSet, DetectorStatus

KINDS = ("blind", "crash", "absent")


class SeededDefectError(ValueError):
    pass


def parse(spec: str) -> tuple[str, str]:
    kind, _, ident = spec.partition(":")
    if kind not in KINDS or not ident:
        raise SeededDefectError(
            f"seeded defect must look like KIND:DETECTOR with KIND in {KINDS}, got '{spec}'"
        )
    return kind, ident


def apply(dset: DetectorSet, spec: str) -> str:
    """Break one detector of ``dset`` in place. Returns a one-line note for the report."""
    kind, ident = parse(spec)
    if ident not in dset.detectors:
        raise SeededDefectError(f"detector '{ident}' did not load, so the defect '{spec}' cannot be applied")
    if kind == "absent":
        dset.detectors.pop(ident).close()
        st = dset.status[ident]
        dset.status[ident] = DetectorStatus(
            ident, MISSING, "seeded defect: package removed", st.lane, st.categories
        )
        return f"SEEDED DEFECT {spec}: the detector was removed from the build"
    det: Any = dset.detectors[ident]
    if kind == "blind":
        det.detect = lambda frame, ctx: ()
        return f"SEEDED DEFECT {spec}: the detector returns no detections"

    def _boom(frame: Any, ctx: Any) -> Any:
        raise RuntimeError("seeded defect")

    det.detect = _boom
    return f"SEEDED DEFECT {spec}: every detect call raises"
