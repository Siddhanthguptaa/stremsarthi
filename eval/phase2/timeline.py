"""Which lane results paint which frame of a clip: the deadline engine's nearest-result rule, offline.

For frame ``i`` and every active lane the engine takes the nearest ok result by source-frame distance (results
from ``before`` frames earlier to ``after`` frames later are all in the delay buffer), as long as it is within the
lane's maximum age; older or later results contribute only requests whose ``[pts_start, pts_end]`` covers the frame.
A lane with no usable result makes the frame PROTECT (a slate): nothing leaks, but it is counted and reported.

Motion projection is not repeated here: the planner already sweeps moving masks along the track velocity, and the
real-time leg (many-target stream) is the end-to-end check of the engine's own projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .frames import Mask


@dataclass(frozen=True)
class LaneRun:
    seq: int
    pts: float
    ok: bool
    masks: tuple[Mask, ...] = ()
    #: (pts_start, pts_end) validity of each mask request, parallel to ``masks``
    valid: tuple[tuple[float, float], ...] = ()


def masks_for_frame(
    runs_by_lane: Mapping[str, Sequence[LaneRun]],
    index: int,
    pts: float,
    max_age: Mapping[str, int],
    before: int = 6,
    after: int = 30,
) -> tuple[list[Mask], bool]:
    """(masks painted on frame ``index``, protect). ``protect`` is True when any lane has no usable result."""
    out: list[Mask] = []
    protect = False
    for lane, runs in runs_by_lane.items():
        near = [r for r in runs if index - before <= r.seq <= index + after]
        ok_runs = sorted((r for r in near if r.ok), key=lambda r: (abs(r.seq - index), -r.seq))
        age_limit = max_age.get(lane, 12)
        chosen = next((r for r in ok_runs if abs(r.seq - index) <= age_limit), None)
        if chosen is None:
            protect = True
        else:
            out.extend(chosen.masks)
        for r in ok_runs:
            if r is chosen:
                continue
            for m, (a, b) in zip(r.masks, r.valid, strict=False):
                if a - 1e-9 <= pts <= b + 1e-9:
                    out.append(m)
    return out, protect
