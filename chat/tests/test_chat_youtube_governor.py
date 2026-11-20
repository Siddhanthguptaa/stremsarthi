"""Quota governor: arithmetic, budgets, read-only degradation, daily reset and the never-exceed invariant."""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from chat.common import ManualClock
from chat.youtube.governor import UNIT_COSTS, QuotaGovernor, pacific_day, seconds_until_pacific_midnight


def make(clock: ManualClock, **kwargs: object) -> tuple[QuotaGovernor, list[str]]:
    events: list[str] = []
    return QuotaGovernor(clock=clock, on_event=events.append, **kwargs), events  # type: ignore[arg-type]


def test_interval_matches_spec_examples(clock: ManualClock) -> None:
    gov, _ = make(clock, planned_stream_hours=3.0)
    gov.start_stream()
    assert gov.read_interval() == pytest.approx(6.75, abs=0.01)  # 10,800 s / 1,600 calls
    gov2, _ = make(clock, planned_stream_hours=2.0)
    gov2.start_stream()
    assert gov2.read_interval() == pytest.approx(4.5, abs=0.01)
    assert (gov.read_budget, gov.action_budget) == (8000, 2000)


def test_interval_stays_steady_as_stream_progresses(clock: ManualClock) -> None:
    gov, _ = make(clock, planned_stream_hours=3.0)
    gov.start_stream()
    for _ in range(100):  # 100 polls at the governed pace
        assert gov.try_spend("list")
        clock.advance(gov.read_interval())
    assert gov.read_interval() == pytest.approx(6.75, abs=0.01)


def test_interval_floor_and_overrun(clock: ManualClock) -> None:
    gov, _ = make(clock, planned_stream_hours=0.1)  # 360 s / 1600 calls would be 0.2 s: floor applies
    gov.start_stream()
    assert gov.read_interval() == 2.0
    gov2, _ = make(clock, planned_stream_hours=1.0)
    gov2.start_stream()
    while gov2.remaining_read > 5 * 100:  # leave 100 calls
        gov2.try_spend("list")
    clock.advance(7200)  # the stream overran its plan: spread what is left over the 30-minute window
    assert gov2.read_interval() == pytest.approx(1800 / 100, abs=0.01)


def test_actions_degrade_to_read_only_with_one_event(clock: ManualClock) -> None:
    gov, events = make(clock)
    done = 0
    while gov.try_spend("delete"):
        done += 1
    assert done == 90  # 2,000-unit action budget minus the 10 % (200-unit) reserve = 1,800 / 20
    assert not gov.actions_enabled and not gov.can_afford("delete") and not gov.can_afford("ban")
    assert events == ["yt_quota_low"]  # reported once, not per refused action
    assert gov.try_spend("list")  # reads keep working: read-only mode
    assert gov.used_total <= gov.daily_units


def test_ban_costs_200_and_is_limited(clock: ManualClock) -> None:
    gov, _ = make(clock)
    bans = 0
    while gov.try_spend("ban"):
        bans += 1
    assert bans == 9  # 1,800 / 200
    assert gov.used_action == 1800


def test_read_budget_exhaustion_blocks_reads_not_actions(clock: ManualClock) -> None:
    gov, events = make(clock)
    calls = 0
    while gov.try_spend("list"):
        calls += 1
    assert calls == 1600 and not gov.reads_enabled and gov.read_interval() == float("inf")
    assert "yt_quota_exhausted" in events
    assert gov.try_spend("delete")  # action budget is separate


def test_mark_exhausted_spends_the_whole_day(clock: ManualClock) -> None:
    gov, events = make(clock)
    gov.mark_exhausted()
    assert not gov.try_spend("list") and not gov.try_spend("delete")
    assert gov.used_total == gov.daily_units
    assert set(events) == {"yt_quota_exhausted", "yt_quota_low"}


def test_restart_assumes_worst_case(clock: ManualClock) -> None:
    gov = QuotaGovernor.after_restart(clock=clock, planned_stream_hours=3.0)
    assert gov.remaining_read == 6000 and gov.remaining_action == 1500
    gov.start_stream()
    assert gov.read_interval() == pytest.approx(10800 / 1200, abs=0.01)


def test_resets_at_pacific_midnight(clock: ManualClock) -> None:
    gov, events = make(clock)
    gov.mark_exhausted()
    wait = gov.seconds_until_reset()
    assert 0 < wait <= 86400
    clock.advance(wait + 1)
    assert gov.reads_enabled and gov.try_spend("delete") and gov.used_total == 20
    assert pacific_day(clock.wall()) != pacific_day(clock.wall() - wait - 1)
    events.clear()
    gov.mark_exhausted()
    assert "yt_quota_exhausted" in events  # events are re-armed for the new day


def test_seconds_until_midnight_is_dst_aware() -> None:
    # 2026-07-01 12:00 Pacific (PDT, UTC-7) is 19:00 UTC: 12 h to midnight
    assert seconds_until_pacific_midnight(1782932400.0) == pytest.approx(12 * 3600, abs=1)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from(["list", "lookup", "delete", "ban"]), max_size=3000), st.integers(0, 2**31))
def test_never_exceeds_daily_budget(ops: list[str], seed: int) -> None:
    clock = ManualClock()
    rng = random.Random(seed)
    gov = QuotaGovernor(clock=clock, initial_used_fraction=rng.choice([0.0, 0.25, 0.9]))
    for op in ops:
        gov.try_spend(op)  # type: ignore[arg-type]
        assert gov.used_read <= gov.read_budget
        assert gov.used_action <= gov.action_budget - gov.action_reserve or gov.used_action == int(
            gov.action_budget * gov._initial_used_fraction
        )
        assert gov.used_total <= gov.daily_units


def test_unit_costs_match_the_quota_table() -> None:
    assert UNIT_COSTS == {"list": 5, "lookup": 1, "delete": 20, "ban": 200}


def test_governor_from_config_matches_the_spec_pacing() -> None:
    from contracts.config import YoutubeChatConfig

    for hours, expect in ((3.0, 6.75), (2.0, 4.5)):
        gov = QuotaGovernor.from_config(YoutubeChatConfig(planned_stream_hours=hours))
        assert gov.action_budget == 2000 and gov.read_budget == 8000
        assert abs(gov.read_interval() - expect) < 0.1
    zero = QuotaGovernor.from_config(YoutubeChatConfig(action_budget_fraction=0.0))
    assert not zero.can_afford("delete")  # 0 means: never act, read-only
    restarted = QuotaGovernor.from_config(YoutubeChatConfig(), restarted=True)
    assert restarted.used_read == 2000 and restarted.used_action == 500
