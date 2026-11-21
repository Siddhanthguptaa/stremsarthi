"""Risk meter levels with downward hysteresis (spec 5.5)."""

from __future__ import annotations

from typing import get_args

from contracts.events import RiskLevel

LEVELS: tuple[RiskLevel, ...] = get_args(RiskLevel)  # green, yellow, orange, red
LOWER_BOUNDS: tuple[float, ...] = (0.0, 0.25, 0.50, 0.75)
HYSTERESIS = 0.05


def raw_level_index(score: float) -> int:
    idx = 0
    for i, bound in enumerate(LOWER_BOUNDS):
        if score >= bound:
            idx = i
    return idx


class LevelTracker:
    """Goes up as soon as the score reaches a band. Leaves a band downward only when the score is
    ``HYSTERESIS`` below that band's lower bound."""

    def __init__(self, hysteresis: float = HYSTERESIS) -> None:
        self.hysteresis = hysteresis
        self._idx = 0

    @property
    def level(self) -> RiskLevel:
        return LEVELS[self._idx]

    @property
    def index(self) -> int:
        return self._idx

    def update(self, score: float) -> RiskLevel:
        raw = raw_level_index(score)
        if raw > self._idx:
            self._idx = raw
        elif raw < self._idx and score < LOWER_BOUNDS[self._idx] - self.hysteresis:
            self._idx = raw
        return self.level
