"""Control: per-source holds, HOLD > PROTECT > LIVE, public snapshot, retro windows.

Ported from IRL Shield tests/test_control.py (MIT) to the HoldSource-keyed API.
"""

import pytest

from contracts import HoldSource
from engine.control import HOLD, LIVE, PROTECT, RETRO_SOURCES, Control


def test_hold_overrides_coverage_and_release_restores_current_coverage():
    control = Control()
    assert (control.state, control.reason) == (PROTECT, "startup")
    assert control.transitions == []
    assert control.update_coverage(True, "fresh", at=10) == LIVE
    assert control.set_hold(HoldSource.HOTKEY, True, reason="manual", at=11) == HOLD
    assert control.set_hold(HoldSource.HOTKEY, True, reason="manual", at=11.5) == HOLD
    assert control.update_coverage(False, "accurate-stale", at=12) == HOLD
    assert control.reason == "manual"
    assert control.set_hold(HoldSource.HOTKEY, False, at=13) == PROTECT
    assert control.reason == "accurate-stale"
    assert control.update_coverage(True, "recovered", at=14) == LIVE
    assert control.transitions == [
        {"from": PROTECT, "to": LIVE, "reason": "fresh", "at": 10},
        {"from": LIVE, "to": HOLD, "reason": "manual", "at": 11},
        {"from": HOLD, "to": PROTECT, "reason": "accurate-stale", "at": 13},
        {"from": PROTECT, "to": LIVE, "reason": "recovered", "at": 14},
    ]


def test_each_hold_source_must_release_before_coverage_can_resume_video():
    control = Control()
    assert control.set_hold(HoldSource.HOTKEY, True, reason="manual", at=1) == HOLD
    assert control.set_hold(HoldSource.CHAT, True, reason="moderator", at=2) == HOLD
    assert control.reason == "moderator"
    assert control.update_coverage(False, "accurate-stale", at=3) == HOLD
    assert control.set_hold(HoldSource.HOTKEY, False, at=4) == HOLD
    assert control.reason == "moderator"
    assert control.set_hold(HoldSource.API, False, at=5) == HOLD  # releasing an unheld source is a no-op
    assert control.update_coverage(True, "fresh", at=6) == HOLD
    assert control.set_hold(HoldSource.CHAT, False, at=7) == LIVE
    assert control.set_hold(HoldSource.CHAT, False, at=8) == LIVE
    assert control.transitions == [
        {"from": PROTECT, "to": HOLD, "reason": "manual", "at": 1},
        {"from": HOLD, "to": LIVE, "reason": "fresh", "at": 7},
    ]


def test_two_clients_pressing_hold_at_once_and_releasing_one():
    control = Control()
    control.update_coverage(True, "fresh")
    control.set_hold(HoldSource.HOTKEY, True)
    control.set_hold(HoldSource.APP, True)
    assert control.set_hold(HoldSource.HOTKEY, False) == HOLD
    assert control.snapshot().hold_sources == (HoldSource.APP,)
    assert control.set_hold(HoldSource.APP, False) == LIVE


def test_snapshot_is_public_immutable_and_ordered():
    control = Control()
    control.update_coverage(True, "covered")
    control.set_hold(HoldSource.AUTO_TRANSPORT, True)
    control.set_hold(HoldSource.API, True)
    control.set_hold(HoldSource.AUTO_TRANSPORT, True)  # re-assert moves it last
    snap = control.snapshot()
    assert snap.state == HOLD and snap.hold_active
    assert snap.hold_sources == (HoldSource.API, HoldSource.AUTO_TRANSPORT)
    assert snap.reason == "auto_transport"
    assert snap.coverage_ok and snap.coverage_reason == "covered"
    with pytest.raises(AttributeError):
        snap.state = "LIVE"  # type: ignore[misc]
    control.set_hold(HoldSource.API, False)
    assert snap.hold_sources == (HoldSource.API, HoldSource.AUTO_TRANSPORT)  # old snapshot unchanged
    assert control.is_held(HoldSource.AUTO_TRANSPORT) and not control.is_held(HoldSource.API)


def test_string_sources_are_coerced_and_unknown_rejected():
    control = Control()
    assert control.set_hold("hotkey", True) == HOLD
    assert control.is_held(HoldSource.HOTKEY)
    with pytest.raises(ValueError):
        control.set_hold("tailscale", True)


def test_transition_ledger_is_bounded():
    control = Control(max_transitions=4)
    for i in range(20):
        control.update_coverage(i % 2 == 0, "x", at=i)
    assert len(control.transitions) == 4


@pytest.mark.parametrize("source", [HoldSource.HOTKEY, HoldSource.API, HoldSource.APP, HoldSource.CHAT])
def test_person_sources_record_a_retro_window_once_per_assertion(source):
    control = Control(retro_seconds=3.0)
    control.set_hold(source, True, at=100.0)
    control.set_hold(source, True, at=101.0)  # already held: no new window
    windows = control.take_retro_windows()
    assert len(windows) == 1
    assert (windows[0].source, windows[0].t_from, windows[0].t_to) == (source, 97.0, 100.0)
    assert control.take_retro_windows() == []
    control.set_hold(source, False, at=102.0)
    control.set_hold(source, True, at=103.0)
    assert len(control.take_retro_windows()) == 1


@pytest.mark.parametrize("source", [HoldSource.AUTO_TRANSPORT, HoldSource.AUTO_EGRESS])
def test_auto_sources_never_retro(source):
    control = Control(retro_seconds=3.0)
    control.set_hold(source, True, at=100.0)
    assert control.take_retro_windows() == []
    assert source not in RETRO_SOURCES


def test_zero_retro_records_nothing_and_negative_rejected():
    control = Control(retro_seconds=0.0)
    control.set_hold(HoldSource.HOTKEY, True, at=5.0)
    assert control.take_retro_windows() == []
    with pytest.raises(ValueError):
        Control(retro_seconds=-1.0)
