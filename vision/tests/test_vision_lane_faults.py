"""SS_FAULT hook in ``LaneWorker`` (protocol: eval/fault.py): lane hang, lane crash, dormant when unset."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from contracts import Frame, Lane
from contracts.fakes import FakeDetector
from vision.lanes import LaneSchedule, LaneWorker, _parse_lane_faults


def _frame(seq: int, arrival: float | None = None) -> Frame:
    return Frame(
        seq=seq, pts=seq / 30.0, arrival=time.monotonic() if arrival is None else arrival, image=None
    )


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.002)
    raise AssertionError("timed out waiting for condition")


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def fault_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "fault.jsonl"
    monkeypatch.setenv("SS_FAULT_LOG", str(log))
    return log


def test_dormant_without_ss_fault(monkeypatch: pytest.MonkeyPatch, fault_log: Path) -> None:
    monkeypatch.delenv("SS_FAULT", raising=False)
    det = FakeDetector()
    w = LaneWorker(Lane.ACCURATE, [det], LaneSchedule())
    assert w._fault is None
    w.start()
    try:
        for seq in range(3):
            w.offer(_frame(seq))
            _wait(lambda s=seq: w.completed >= s + 1)
        results = w.drain()
    finally:
        w.close()
    assert [r.ok for r in results] == [True, True, True] and w.failed == 0
    assert _records(fault_log) == []


def test_other_lane_or_sink_spec_leaves_worker_untouched(
    monkeypatch: pytest.MonkeyPatch, fault_log: Path
) -> None:
    monkeypatch.setenv("SS_FAULT", "lane:accurate:crash@t=0s;sink:stall@t=0s;lane:fast:bogus@t=0")
    w = LaneWorker(Lane.FAST, [FakeDetector()], LaneSchedule())
    assert w._fault is None
    w.start()
    try:
        w.offer(_frame(0))
        _wait(lambda: w.completed >= 1)
    finally:
        w.close()
    assert w.failed == 0 and _records(fault_log) == []


def test_parser_accepts_protocol_forms_and_ignores_garbage() -> None:
    specs = _parse_lane_faults(
        "lane:fast:crash@t=15s,for=5s; lane:fast:hang@t=500ms;lane:fast:crash@x;nope", "fast"
    )
    assert [(s.mode, s.at_s, s.for_s) for s in specs] == [("crash", 15.0, 5.0), ("hang", 0.5, None)]
    assert specs[0].canonical("fast") == "lane:fast:crash@t=15s,for=5s"
    assert _parse_lane_faults("lane:accurate:hang@t=20", "fast") == []
    assert _parse_lane_faults("lane:fast:crash@t=1s,for=0s", "fast") == []
    assert _parse_lane_faults(None, "fast") == []


def test_crash_makes_every_run_ok_false_and_logs_once(
    monkeypatch: pytest.MonkeyPatch, fault_log: Path
) -> None:
    monkeypatch.setenv("SS_FAULT", "lane:fast:crash@t=0")
    det = FakeDetector()
    w = LaneWorker(Lane.FAST, [det], LaneSchedule())
    t_first = time.monotonic()
    w.start()
    try:
        for seq in range(3):
            w.offer(_frame(seq, arrival=t_first))
            _wait(lambda s=seq: w.failed >= s + 1)
        results = w.drain()
    finally:
        w.close()
    assert [r.ok for r in results] == [False, False, False]
    assert [r.frame_seq for r in results] == [0, 1, 2]
    assert w.completed == 0 and det.calls == []  # crashed before the detector
    assert w.last_error == {"fault": "RuntimeError"}  # type name only, as for real failures
    recs = _records(fault_log)
    assert len(recs) == 1
    rec = recs[0]
    assert (
        rec["event"] == "fault_active" and rec["spec"] == "lane:fast:crash@t=0s" and rec["proc"] == "vision"
    )
    assert rec["t_monotonic"] == pytest.approx(t_first, abs=1e-3)  # edge = t0 + at_s on the monotonic clock


def test_crash_starts_at_t_and_clears_after_for(monkeypatch: pytest.MonkeyPatch, fault_log: Path) -> None:
    monkeypatch.setenv("SS_FAULT", "lane:fast:crash@t=200ms,for=300ms")
    det = FakeDetector()
    w = LaneWorker(Lane.FAST, [det], LaneSchedule())
    t0 = time.monotonic()
    w.start()
    try:
        w.offer(_frame(0, arrival=t0))  # before t: runs normally
        _wait(lambda: w.completed >= 1)
        time.sleep(max(0.0, t0 + 0.25 - time.monotonic()))
        w.offer(_frame(1, arrival=t0))  # inside the window
        _wait(lambda: w.failed >= 1)
        time.sleep(max(0.0, t0 + 0.6 - time.monotonic()))
        w.offer(_frame(2, arrival=t0))  # after the window: normal again
        _wait(lambda: w.completed >= 2)
        results = w.drain()
    finally:
        w.close()
    assert [r.ok for r in results] == [True, False, True]
    assert [(r["event"], r["spec"]) for r in _records(fault_log)] == [
        ("fault_active", "lane:fast:crash@t=0.2s,for=0.3s"),
        ("fault_cleared", "lane:fast:crash@t=0.2s,for=0.3s"),
    ]


def test_hang_blocks_before_detector_with_no_result_and_close_still_returns(
    monkeypatch: pytest.MonkeyPatch, fault_log: Path
) -> None:
    monkeypatch.setenv("SS_FAULT", "lane:accurate:hang@t=0s")
    det = FakeDetector()
    w = LaneWorker(Lane.ACCURATE, [det], LaneSchedule())
    t_first = time.monotonic()
    w.start()
    w.offer(_frame(0, arrival=t_first))
    _wait(lambda: len(_records(fault_log)) == 1)
    time.sleep(0.15)
    assert w.drain() == [] and det.calls == [] and w.completed == 0 and w.failed == 0  # hung, not failed
    assert w._thread is not None and w._thread.is_alive() and w._thread.daemon
    rec = _records(fault_log)[0]
    assert (
        rec["event"] == "fault_active"
        and rec["spec"] == "lane:accurate:hang@t=0s"
        and rec["proc"] == "vision"
    )
    assert rec["t_monotonic"] == pytest.approx(t_first, abs=1e-3)
    t_close = time.monotonic()
    with pytest.raises(
        RuntimeError, match="did not stop"
    ):  # close() returns (bounded), reporting the hung lane
        w.close(timeout=0.2)
    assert time.monotonic() - t_close < 2.0
    assert not det.closed  # never close detectors under a running worker


def test_hang_does_not_touch_other_lane(monkeypatch: pytest.MonkeyPatch, fault_log: Path) -> None:
    monkeypatch.setenv("SS_FAULT", "lane:accurate:hang@t=0")
    fast = LaneWorker(Lane.FAST, [FakeDetector()], LaneSchedule())
    acc = LaneWorker(Lane.ACCURATE, [FakeDetector()], LaneSchedule())
    fast.start()
    acc.start()
    try:
        for seq in (0, 1):
            fast.offer(_frame(seq))
            acc.offer(_frame(seq))
            _wait(lambda s=seq: fast.completed >= s + 1)
        assert [r.ok for r in fast.drain()] == [True, True]
        assert acc.drain() == []
    finally:
        fast.close()
        with pytest.raises(RuntimeError):
            acc.close(timeout=0.1)
