"""Lane scheduling: stride, rate, newest-wins, failure => ok=False, one LaneResult per run (CPU only)."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from contracts import Category, DetectConfig, DetectContext, Detection, Frame, Lane, Quad
from contracts.fakes import FakeDetector
from vision.lanes import LaneSchedule, LaneWorker


def _frame(seq: int) -> Frame:
    return Frame(seq=seq, pts=seq / 30.0, arrival=time.monotonic(), image=None)


def _det(seq: int = 0) -> Detection:
    return Detection("fake", Category.AADHAAR, Quad.from_xyxy(1, 1, 50, 30), 0.9, seq, 0.0, 0.0)


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.002)
    raise AssertionError("timed out waiting for condition")


def _collect(worker: LaneWorker, n: int) -> list:
    out: list = []
    _wait(lambda: out.extend(worker.drain()) or len(out) >= n)
    return out


def test_schedule_from_config() -> None:
    d = DetectConfig(stride=3, accurate_hz=4.0)
    assert LaneSchedule.from_config(Lane.FAST, d) == LaneSchedule(stride=3, rate_hz=None)
    assert LaneSchedule.from_config(Lane.ACCURATE, d) == LaneSchedule(stride=1, rate_hz=4.0)
    assert LaneSchedule.from_config(Lane.SLOW, d).rate_hz == 1.0
    with pytest.raises(ValueError):
        LaneSchedule(stride=0)
    with pytest.raises(ValueError):
        LaneSchedule(rate_hz=0)


def test_stride_three_processes_exactly_frames_0_3_6() -> None:
    det = FakeDetector()
    w = LaneWorker(Lane.FAST, [det], LaneSchedule(stride=3))
    w.start()
    try:
        for seq in range(9):  # 0..8, waiting for each accepted frame so none are dropped by newest-wins
            if w.offer(_frame(seq)):
                _wait(lambda s=seq: w.completed >= (s // 3) + 1)
        results = _collect(w, 3)
    finally:
        w.close()
    assert det.calls == [0, 3, 6]
    assert [r.frame_seq for r in results] == [0, 3, 6]
    assert all(r.ok and r.lane is Lane.FAST for r in results)
    assert w.skipped == 6 and w.dropped == 0 and det.closed


def test_rate_limits_runs_and_newest_frame_wins() -> None:
    det = FakeDetector()
    w = LaneWorker(Lane.ACCURATE, [det], LaneSchedule(rate_hz=10.0))  # one run per 100 ms
    w.start()
    t0 = time.monotonic()
    seq = 0
    try:
        while time.monotonic() - t0 < 0.55:  # offer at ~200 Hz
            w.offer(_frame(seq))
            seq += 1
            time.sleep(0.005)
        _wait(lambda: w.completed >= 5)
    finally:
        w.close()
    assert 5 <= len(det.calls) <= 8  # about 10 Hz over 0.55 s, not 100+
    assert w.dropped > 50  # work was dropped, never queued
    assert det.calls == sorted(det.calls)  # always the newest available frame, in order
    gaps = [b - a for a, b in zip(det.calls, det.calls[1:], strict=False)]
    assert min(gaps) >= 10  # >= ~100 ms of 5 ms offers between runs


def test_raising_detector_gives_ok_false_then_recovers() -> None:
    det = FakeDetector(script={0: [_det()], 2: [_det()]}, raise_on_seq=1)
    w = LaneWorker(Lane.FAST, [det], LaneSchedule())
    w.start()
    try:
        for seq in range(3):
            w.offer(_frame(seq))
            _wait(lambda s=seq: w.completed + w.failed >= s + 1)
        results = _collect(w, 3)
    finally:
        w.close()
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].requests == () and results[1].frame_seq == 1
    assert w.failed == 1 and w.last_error == {"fake": "RuntimeError"}
    assert "scripted" not in repr(w.last_error)  # exception text is never kept


def test_every_run_yields_a_lane_result_even_when_empty() -> None:
    w = LaneWorker(Lane.FAST, [FakeDetector()], LaneSchedule())
    w.start()
    try:
        w.offer(_frame(0))
        (r,) = _collect(w, 1)
    finally:
        w.close()
    assert r.ok and r.requests == () and r.aux_detections == ()


def test_zero_detectors_still_heartbeat() -> None:
    w = LaneWorker(Lane.ACCURATE, [], LaneSchedule(rate_hz=50.0))
    w.start()
    try:
        w.offer(_frame(0))
        (r,) = _collect(w, 1)
    finally:
        w.close()
    assert r.ok and r.lane is Lane.ACCURATE


def test_second_detector_not_run_after_first_fails_and_assembler_gets_detections() -> None:
    seen: list[tuple[int, int]] = []

    def assembler(frame: Frame, lane: Lane, dets: tuple[Detection, ...]):
        seen.append((frame.seq, len(dets)))
        return (), ()

    bad = FakeDetector(raise_on_seq=0)
    good = FakeDetector(script={0: [_det()], 1: [_det(), _det()]})
    w = LaneWorker(Lane.FAST, [bad, good], LaneSchedule(), assembler=assembler)
    w.start()
    try:
        w.offer(_frame(0))
        _wait(lambda: w.failed == 1)
        assert good.calls == []  # fail fast
        bad.raise_on_seq = None
        w.offer(_frame(1))
        _wait(lambda: w.completed == 1)
    finally:
        w.close()
    assert seen == [(1, 2)]


def test_failing_assembler_marks_run_not_ok() -> None:
    def assembler(frame: Frame, lane: Lane, dets: tuple[Detection, ...]):
        raise ValueError("planner broke")

    w = LaneWorker(Lane.FAST, [FakeDetector()], LaneSchedule(), assembler=assembler)
    w.start()
    try:
        w.offer(_frame(0))
        (r,) = _collect(w, 1)
    finally:
        w.close()
    assert not r.ok and w.last_error == {"assembler": "ValueError"}


def test_context_carries_previous_detections_and_stride() -> None:
    ctxs: list[DetectContext] = []

    class Spy(FakeDetector):
        def detect(self, frame: Frame, ctx: DetectContext):
            ctxs.append(ctx)
            return super().detect(frame, ctx)

    det = Spy(script={0: [_det()]})
    w = LaneWorker(Lane.FAST, [det], LaneSchedule(stride=3), deadline_s=0.5)
    w.start()
    try:
        w.offer(_frame(0))
        _wait(lambda: w.completed == 1)
        w.offer(_frame(3))
        _wait(lambda: w.completed == 2)
    finally:
        w.close()
    assert ctxs[0].previous == () and ctxs[0].stride == 3 and ctxs[0].deadline is not None
    assert len(ctxs[1].previous) == 1 and ctxs[1].lane is Lane.FAST


def test_slow_accurate_lane_does_not_block_fast_lane() -> None:
    slow = LaneWorker(Lane.ACCURATE, [FakeDetector(latency_s=0.4)], LaneSchedule(rate_hz=4.0))
    fast = LaneWorker(Lane.FAST, [FakeDetector()], LaneSchedule(stride=3))
    slow.start()
    fast.start()
    try:
        t0 = time.monotonic()
        for seq in range(30):  # 1 s of 30 fps
            f = _frame(seq)
            slow.offer(f)
            fast.offer(f)
            time.sleep(1 / 30)
        elapsed = time.monotonic() - t0
        _wait(lambda: fast.completed >= 10)
    finally:
        slow.close()
        fast.close()
    assert fast.completed == 10  # frames 0,3,...,27 all processed on time
    assert slow.completed <= 3 < fast.completed  # the slow lane dropped work
    assert slow.dropped >= 10 and elapsed < 1.5


def test_offer_after_close_raises_and_double_start_raises() -> None:
    w = LaneWorker(Lane.FAST, [], LaneSchedule())
    w.start()
    with pytest.raises(RuntimeError):
        w.start()
    w.close()
    with pytest.raises(RuntimeError):
        w.offer(_frame(0))
