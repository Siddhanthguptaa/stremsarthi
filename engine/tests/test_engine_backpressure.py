"""Back-pressure helpers (RequestTracker, stale-drop, clamp_region)."""

import threading

import pytest

from engine.backpressure import RequestTracker, clamp_region, is_stale, is_stale_request


def test_tracker_limits_concurrency_and_never_goes_negative():
    tracker = RequestTracker(2)
    assert tracker.can_start()
    assert tracker.try_start() and tracker.try_start()
    assert not tracker.try_start() and not tracker.can_start()
    assert tracker.finish() == 1
    assert tracker.try_start()
    for _ in range(5):
        tracker.finish()
    assert tracker.active == 0
    tracker.configure(0)
    assert tracker.max_concurrent == 1  # never below one


def test_tracker_context_manager_releases_on_error():
    tracker = RequestTracker(1)
    with pytest.raises(RuntimeError), tracker.track():
        assert tracker.active == 1
        raise RuntimeError("boom")
    assert tracker.active == 0


def test_tracker_is_thread_safe():
    tracker = RequestTracker(8)
    won = []

    def grab() -> None:
        won.append(tracker.try_start())

    threads = [threading.Thread(target=grab) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(won) == 8 and tracker.active == 8


def test_stale_request_rules():
    assert not is_stale_request(1000, 500, current_time_ms=1400)
    assert is_stale_request(1000, 500, current_time_ms=1501)
    assert is_stale_request("garbage", 500)
    assert is_stale_request(None, 500)
    assert not is_stale_request("garbage", 500, enabled=False)


def test_monotonic_stale_variant():
    assert not is_stale(10.0, 1.0, now=10.5)
    assert is_stale(10.0, 1.0, now=11.5)
    assert is_stale(float("nan"), 1.0, now=1.0)


def test_clamp_region():
    shape = (720, 1280, 3)
    assert clamp_region(shape, [10, 20, 100, 200]) == (10, 20, 100, 200)
    assert clamp_region(shape, [100, 200, 10, 20]) == (10, 20, 100, 200)  # swapped corners
    assert clamp_region(shape, [-50, -50, 2000, 2000]) == (0, 0, 1280, 720)
    assert clamp_region(shape, [10, 10, 10, 50]) is None  # empty
    assert clamp_region(shape, [2000, 0, 3000, 10]) is None  # fully outside
    assert clamp_region(shape, [1, 2, 3]) is None
    assert clamp_region(shape, ["a", 2, 3, 4]) is None
    assert clamp_region(shape, [float("inf"), 0, 5, 5]) is None
