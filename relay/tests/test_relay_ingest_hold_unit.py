"""Transport-hold logic of ``NetworkInput`` driven by a fake clock and bus (CPU only, no media, no threads)."""

from __future__ import annotations

from contracts.enums import HoldSource, Topic
from relay.ingest import NetworkInput
from relay.tests.support_media import TimedBus


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _source(hold_after_s: float = 1.0) -> tuple[NetworkInput, TimedBus, Clock]:
    bus, clock = TimedBus(), Clock()
    source = NetworkInput(
        "rtmp://127.0.0.1:1/x", bus=bus, hold_after_s=hold_after_s, clock=clock, start=False
    )
    return source, bus, clock


def _holds(bus: TimedBus) -> list[bool]:
    return [e.active for e in bus.of(Topic.HOLD)]


def _frames(source: NetworkInput, clock: Clock, n: int, spacing: float = 1 / 30) -> None:
    for _ in range(n):
        clock.t += spacing
        source._note_media(clock.t)


def test_steady_frames_never_hold():
    source, bus, clock = _source()
    _frames(source, clock, 90)
    assert _holds(bus) == [] and not source.hold_active


def test_a_stall_longer_than_hold_after_asserts_and_two_frames_release():
    source, bus, clock = _source(hold_after_s=1.0)
    _frames(source, clock, 30)
    clock.t += 1.2
    source._note_media(clock.t)  # first frame after the stall: it asserts the hold, it does not release it
    assert _holds(bus) == [True] and source.hold_active
    _frames(source, clock, 1)
    assert _holds(bus) == [True, False] and not source.hold_active
    hold = bus.of(Topic.HOLD)[0]
    assert hold.source == HoldSource.AUTO_TRANSPORT


def test_a_stall_shorter_than_hold_after_does_not_hold():
    source, bus, clock = _source(hold_after_s=1.0)
    _frames(source, clock, 30)
    clock.t += 0.9
    _frames(source, clock, 30)
    assert _holds(bus) == []


def test_watchdog_asserts_while_no_frame_arrives():
    source, bus, clock = _source(hold_after_s=1.0)
    _frames(source, clock, 30)
    clock.t += 1.5
    last = source._last_media
    assert last is not None and clock() - last > 1.0  # what the watchdog thread tests every 50 ms
    source._assert_hold()
    assert _holds(bus) == [True]


def test_transport_event_asserts_at_once_and_recovery_needs_fresh_frames():
    source, bus, clock = _source(hold_after_s=1.0)
    _frames(source, clock, 30)
    source.notify_transport_event("publisher-disconnected")
    assert _holds(bus) == [True] and source.hold_active
    assert source.stats()["transport_event_publisher_disconnected"] == 1
    _frames(source, clock, 1)
    assert source.hold_active, "one frame is not recovery"
    _frames(source, clock, 1)
    assert _holds(bus) == [True, False]


def test_transport_event_is_ignored_before_media_for_unknown_codes_and_when_closed():
    source, bus, clock = _source()
    source.notify_transport_event("publisher-disconnected")  # nothing to protect yet
    assert _holds(bus) == []
    _frames(source, clock, 5)
    source.notify_transport_event("srt-loss")  # a packet-loss log line is not a drop
    source.notify_transport_event("auth-failure")
    assert _holds(bus) == []
    source.close()
    source.notify_transport_event("mediamtx-exit")
    assert _holds(bus) == []


def test_repeated_events_assert_the_hold_once():
    source, bus, clock = _source()
    _frames(source, clock, 5)
    for _ in range(3):
        source.notify_transport_event("publisher-disconnected")
    assert _holds(bus) == [True]
