"""YouTube Data API quota governor (10,000 units per day, resets at midnight Pacific).

Budget (docs/specs/chat.md 5.2): ``action_budget_fraction`` (0.2) of the day is kept for actions
(2,000 units), the rest (8,000) is for reads. Costs: list 5, delete 20, ban 200, lookup 1.

Guarantees, all covered by tests:

* ``try_spend`` charges the cost *before* the call (conservative: a failed request is counted too) and
  refuses when the bucket would go over its budget, so the counter never exceeds ``daily_units``.
* Actions stop while less than ``low_action_fraction`` (10 %) of the action budget remains. That
  reserve is never spent; it absorbs drift between this counter and Google's. The governor then
  degrades to read-only (hide on our side) and reports ``yt_quota_low`` once.
* A 403 ``quotaExceeded`` from Google marks the whole day spent (``mark_exhausted``).
* The read interval spreads the remaining read budget over the remaining planned stream time.

State is in memory only. A restarted process cannot know what was spent earlier today, so
``QuotaGovernor.after_restart`` starts with 25 % of both budgets assumed spent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chat.common.clock import Clock, SystemClock
from chat.common.logsafe import log_event
from contracts.config import YoutubeChatConfig

Kind = Literal["list", "lookup", "delete", "ban"]
UNIT_COSTS: dict[str, int] = {"list": 5, "lookup": 1, "delete": 20, "ban": 200}
_ACTION_KINDS = frozenset({"delete", "ban"})
LOW_CODE = "yt_quota_low"
EXHAUSTED_CODE = "yt_quota_exhausted"

_LOG = logging.getLogger("chat.youtube.governor")

try:
    _PACIFIC: timezone | ZoneInfo = ZoneInfo("America/Los_Angeles")
except ZoneInfoNotFoundError:  # no tz database: fixed offset, off by one hour during DST only
    _PACIFIC = timezone(timedelta(hours=-8))


def pacific_day(wall_ts: float) -> date:
    return datetime.fromtimestamp(wall_ts, _PACIFIC).date()


def seconds_until_pacific_midnight(wall_ts: float) -> float:
    local = datetime.fromtimestamp(wall_ts, _PACIFIC)
    nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0.0, (nxt - local).total_seconds())


class QuotaGovernor:
    def __init__(
        self,
        *,
        daily_units: int = 10_000,
        action_budget_fraction: float = 0.2,
        planned_stream_hours: float = 3.0,
        min_poll_s: float = 2.0,
        low_action_fraction: float = 0.10,
        initial_used_fraction: float = 0.0,
        overrun_window_s: float = 1800.0,
        clock: Clock | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        if daily_units <= 0 or not 0 < action_budget_fraction < 1:
            raise ValueError("invalid quota parameters")
        if planned_stream_hours <= 0 or min_poll_s <= 0 or not 0 <= low_action_fraction < 1:
            raise ValueError("invalid quota parameters")
        if not 0 <= initial_used_fraction < 1:
            raise ValueError("initial_used_fraction must be within [0, 1)")
        self.daily_units = daily_units
        self.action_budget = int(daily_units * action_budget_fraction)
        self.read_budget = daily_units - self.action_budget
        self.low_action_fraction = low_action_fraction
        self.planned_stream_s = planned_stream_hours * 3600.0
        self.min_poll_s = min_poll_s
        self.overrun_window_s = overrun_window_s
        self._initial_used_fraction = initial_used_fraction
        self._clock: Clock = clock or SystemClock()
        self._on_event = on_event
        self._stream_started: float | None = None
        self._day = pacific_day(self._clock.wall())
        self._reset_counters()

    @classmethod
    def from_config(
        cls,
        config: YoutubeChatConfig,
        *,
        restarted: bool = False,
        clock: Clock | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> QuotaGovernor:
        """Build from ``Config.chat.youtube``. ``restarted`` assumes 25 % already spent (see below)."""
        kwargs: dict[str, object] = {
            "daily_units": config.daily_quota_units,
            "action_budget_fraction": max(config.action_budget_fraction, 0.001),  # 0 means "no actions"
            "planned_stream_hours": config.planned_stream_hours,
            "min_poll_s": config.min_poll_s,
            "clock": clock,
            "on_event": on_event,
        }
        return cls.after_restart(**kwargs) if restarted else cls(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def after_restart(cls, **kwargs: object) -> QuotaGovernor:
        """A process restart loses the counter: assume the worst (25 % of each budget already spent)."""
        kwargs.setdefault("initial_used_fraction", 0.25)
        return cls(**kwargs)  # type: ignore[arg-type]

    # ---- counters -----------------------------------------------------------------------------
    def _reset_counters(self) -> None:
        self.used_read = int(self.read_budget * self._initial_used_fraction)
        self.used_action = int(self.action_budget * self._initial_used_fraction)
        self._exhausted = False
        self._emitted: set[str] = set()

    def _roll(self) -> None:
        day = pacific_day(self._clock.wall())
        if day != self._day:
            self._day = day
            self._initial_used_fraction = 0.0  # a new quota day starts clean
            self._reset_counters()
            log_event(_LOG, "quota_day_rolled")

    def _emit(self, code: str) -> None:
        if code in self._emitted:
            return
        self._emitted.add(code)
        log_event(_LOG, "quota_event", level=logging.WARNING, code=code)
        if self._on_event is not None:
            try:
                self._on_event(code)
            except Exception:  # a sink must never break accounting
                log_event(_LOG, "quota_sink_failed", level=logging.WARNING)

    @property
    def used_total(self) -> int:
        return self.used_read + self.used_action

    @property
    def remaining_read(self) -> int:
        self._roll()
        return max(0, self.read_budget - self.used_read)

    @property
    def remaining_action(self) -> int:
        self._roll()
        return max(0, self.action_budget - self.used_action)

    @property
    def action_reserve(self) -> int:
        return int(self.action_budget * self.low_action_fraction)

    @property
    def actions_enabled(self) -> bool:
        """False once the action budget is down to its reserve (or the day is spent)."""
        self._roll()
        return not self._exhausted and self.remaining_action > self.action_reserve

    @property
    def reads_enabled(self) -> bool:
        self._roll()
        if not self._exhausted and self.remaining_read < UNIT_COSTS["list"]:
            self._emit(EXHAUSTED_CODE)  # the read budget ran out without a refused call: still report it
            return False
        return not self._exhausted

    # ---- spending -----------------------------------------------------------------------------
    def try_spend(self, kind: Kind) -> bool:
        """Charge ``kind`` before issuing the call. False means do not call."""
        self._roll()
        cost = UNIT_COSTS[kind]
        if self._exhausted:
            return False
        if kind in _ACTION_KINDS:
            if self.used_action + cost > self.action_budget - self.action_reserve:
                self._emit(LOW_CODE)
                return False
            self.used_action += cost
            if self.remaining_action <= self.action_reserve + cost:
                # the next action of this size would be refused: tell the caller early
                self._emit(LOW_CODE)
            return True
        if self.used_read + cost > self.read_budget:
            self._emit(EXHAUSTED_CODE)
            return False
        self.used_read += cost
        return True

    def can_afford(self, kind: Kind) -> bool:
        """Non-charging check (for deciding whether to even try an action)."""
        self._roll()
        if self._exhausted:
            return False
        cost = UNIT_COSTS[kind]
        if kind in _ACTION_KINDS:
            return self.used_action + cost <= self.action_budget - self.action_reserve
        return self.used_read + cost <= self.read_budget

    def mark_exhausted(self) -> None:
        """Google said ``quotaExceeded``: whatever our counter thought, the day is spent."""
        self._roll()
        self._exhausted = True
        self.used_read = max(self.used_read, self.read_budget)
        self.used_action = max(self.used_action, self.action_budget)
        self._emit(EXHAUSTED_CODE)
        self._emit(LOW_CODE)

    # ---- pacing -------------------------------------------------------------------------------
    def start_stream(self) -> None:
        if self._stream_started is None:
            self._stream_started = self._clock.monotonic()

    def read_interval(self) -> float:
        """Seconds between ``list`` calls: remaining planned time / remaining affordable calls.

        At stream start this is ``planned_hours * 3600 / (read_budget / 5)`` (3 h -> 6.75 s). Using
        the remaining *time* (not the full plan) keeps the pace steady as the stream progresses. After
        the plan has elapsed, the leftover budget is spread over ``overrun_window_s``. Never below
        ``min_poll_s``; ``inf`` when no read can be afforded."""
        self._roll()
        calls = self.remaining_read // UNIT_COSTS["list"]
        if calls <= 0 or self._exhausted:
            return float("inf")
        elapsed = 0.0 if self._stream_started is None else self._clock.monotonic() - self._stream_started
        time_left = (
            self.planned_stream_s - elapsed if elapsed < self.planned_stream_s else self.overrun_window_s
        )
        return max(self.min_poll_s, time_left / calls)

    def seconds_until_reset(self) -> float:
        return seconds_until_pacific_midnight(self._clock.wall())

    def snapshot(self) -> dict[str, int | bool]:
        """Counters for logs and tests (numbers only)."""
        return {
            "used_read": self.used_read,
            "used_action": self.used_action,
            "remaining_read": self.remaining_read,
            "remaining_action": self.remaining_action,
            "actions_enabled": self.actions_enabled,
            "reads_enabled": self.reads_enabled,
        }


__all__ = [
    "EXHAUSTED_CODE",
    "LOW_CODE",
    "UNIT_COSTS",
    "QuotaGovernor",
    "pacific_day",
    "seconds_until_pacific_midnight",
]
