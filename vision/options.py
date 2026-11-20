"""Resolve optional detector policy at lane construction time.

Adapted from IRL Shield ``bench/detector_options.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``vision/LICENSE.irl-shield``). Change: lanes are ``contracts.Lane`` values and the SLOW lane is accepted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any

from contracts import Lane


def lane_detector_options(options: Mapping[str, Any], lane: Lane) -> dict[str, Any]:
    """Apply lane-specific policy: ``accurate_box_thresh`` replaces ``box_thresh`` on the ACCURATE lane only."""
    if not isinstance(lane, Lane):
        raise ValueError("lane must be a contracts.Lane")
    resolved = dict(options)
    floor = resolved.pop("accurate_box_thresh", None)
    if floor is not None:
        if (
            isinstance(floor, bool)
            or not isinstance(floor, Real)
            or not 0 <= floor <= 1
            or not math.isfinite(floor)
        ):
            raise ValueError("accurate_box_thresh must be a finite number between 0 and 1, or None")
        if lane is Lane.ACCURATE:
            resolved["box_thresh"] = floor
    return resolved
