"""Single-slot writer semantics (ported from IRL Shield ``tests/integration/test_writer_semantics.py``),
frame ownership, the sink contract and what ``Egress`` publishes. No process, no network, no GPU."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from contracts.enums import Topic
from contracts.fakes.bus import InProcessBus
from contracts.runtime import FrameSink
from relay.destinations import DestinationSet
from relay.egress import Egress, EgressError, EgressWriter
from relay.encoder import KIND_VIDEO, EncodedPacket, EncodeResult
from relay.sink import EgressFrameSink, NullFrameSink

SHAPE = (8, 16)


def frame(value: int) -> np.ndarray:
    return np.full((*SHAPE, 3), value, np.uint8)


def pcm(value: int, n: int = 1600) -> np.ndarray:
    return np.full(n, value, np.int16)


def wait_until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.002)
    return condition()


class Output:
    """A controlled output: the first write blocks until released, so a frame is provably in flight."""

    def __init__(self, outcome=True):
        self.entered, self.release = threading.Event(), threading.Event()
        self.rows: list[tuple[float, int, list]] = []
        self.outcome = outcome
        self.closed = False
        self.block_first = True

    def write(self, pts, image, chunks):
        if self.block_first and not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(3), "fixture release timeout"
        self.rows.append((pts, int(image[0, 0, 0]), [(p, s.copy()) for p, s in chunks]))
        return self.outcome

    def close(self):
        self.closed = True


def test_write_never_blocks_on_the_output_and_keeps_one_pending_slot():
    output = Output()
    writer = EgressWriter(output, shape=SHAPE)
    try:
        writer.write(0.0, frame(1), pcm(1))
        assert output.entered.wait(2), "frame 1 must be in flight"
        started = time.monotonic()
        writer.write(1 / 30, frame(2), pcm(2))  # becomes pending
        writer.write(2 / 30, frame(3), pcm(3))  # replaces pending
        assert time.monotonic() - started < 0.25, "write() waited for the blocked output"
        assert writer.dropped == 1
        output.release.set()
        assert wait_until(lambda: len(output.rows) == 2)
        assert [row[1] for row in output.rows] == [1, 3]  # frame 2's pixels were dropped
        chunks = output.rows[1][2]
        assert [round(p * 30) for p, _ in chunks] == [1, 2]  # but its audio moved on to frame 3
        assert all((s == v).all() for (_, s), v in zip(chunks, (2, 3), strict=True))
    finally:
        output.release.set()
        writer.close()
    assert output.closed and not writer.thread.is_alive()


def test_the_frame_is_copied_so_the_engine_may_reuse_its_buffer():
    output = Output()
    writer = EgressWriter(output, shape=SHAPE)
    try:
        engine_buffer = frame(10)
        writer.write(0.0, engine_buffer, pcm(5))
        assert output.entered.wait(2)
        engine_buffer[:] = 99  # the engine paints the next frame into the same buffer
        output.release.set()
        assert wait_until(lambda: output.rows)
        assert output.rows[0][1] == 10
    finally:
        output.release.set()
        writer.close()


def test_a_slow_success_drops_the_pending_frame_and_anything_presented_before_it():
    clock = [0.0]
    output = Output()

    def slow_write(pts, image, chunks):
        output.rows.append((pts, int(image[0, 0, 0]), []))
        output.entered.set()
        assert output.release.wait(3)
        clock[0] += 1.0  # the write took longer than the freshness budget
        return True

    output.write = slow_write  # type: ignore[method-assign]
    writer = EgressWriter(output, shape=SHAPE, slow_after_s=0.5, clock=lambda: clock[0])
    try:
        writer.write(0.0, frame(1), pcm(1))
        assert output.entered.wait(2)
        writer.write(1 / 30, frame(2), pcm(2))  # pending while the slow write runs
        output.release.set()
        assert wait_until(lambda: writer.slow_writes == 1)
        assert writer.dropped == 1  # the stale pending frame was discarded
        clock[0] = 0.5
        assert writer.write(2 / 30, frame(3), pcm(3)) is True  # presented before the stall ended: dropped
        assert writer.dropped == 2
        clock[0] = 1.0
        writer.write(3 / 30, frame(4), pcm(4))
        assert wait_until(lambda: [r[1] for r in output.rows] == [1, 4])
    finally:
        output.release.set()
        writer.close()


def test_a_false_result_means_recovery_and_is_reported_to_the_next_write():
    output = Output(outcome=False)
    output.block_first = False
    writer = EgressWriter(output, shape=SHAPE)
    try:
        writer.write(0.0, frame(1), pcm(1))
        assert wait_until(lambda: len(output.rows) == 1)
        assert wait_until(lambda: writer.write(1.0, frame(2), pcm(2)) is False)
        output.outcome = True
        assert wait_until(lambda: writer.write(2.0, frame(3), pcm(3)) is True)
    finally:
        writer.close()


def test_an_output_exception_becomes_a_fixed_code_and_still_closes_the_output():
    output = Output()
    output.block_first = False

    def boom(pts, image, chunks):
        raise RuntimeError("rtmp://127.0.0.1:1/live2/SECRET-KEY-1234 refused")

    output.write = boom  # type: ignore[method-assign]
    writer = EgressWriter(output, shape=SHAPE)
    writer.write(0.0, frame(1), pcm(1))
    assert wait_until(lambda: output.closed)
    with pytest.raises(EgressError) as error:
        writer.write(1.0, frame(2), pcm(2))
    assert error.value.code == "egress-writer-failed" and "SECRET" not in str(error.value)
    assert writer.alive is False
    writer.close()


def test_wrong_frame_shape_or_dtype_is_a_contract_error():
    output = Output()
    output.block_first = False
    writer = EgressWriter(output, shape=SHAPE)
    try:
        with pytest.raises(EgressError, match="frame-contract"):
            writer.write(0.0, np.zeros((9, 16, 3), np.uint8), pcm(1))
        with pytest.raises(EgressError, match="frame-contract"):
            writer.write(0.0, np.zeros((*SHAPE, 3), np.float32), pcm(1))
    finally:
        writer.close()


def test_close_is_idempotent_and_refuses_later_writes():
    output = Output()
    output.block_first = False
    writer = EgressWriter(output, shape=SHAPE)
    writer.close()
    writer.close()
    with pytest.raises(EgressError, match="egress-closed"):
        writer.write(0.0, frame(1), pcm(1))


def test_buffer_pool_is_bounded_under_a_steady_stream():
    output = Output()
    output.block_first = False
    writer = EgressWriter(output, shape=SHAPE)
    try:
        for i in range(200):
            writer.write(i / 30, frame(i % 250), pcm(1))
            time.sleep(0.001)
        assert wait_until(lambda: writer.written + writer.dropped >= 200)
        assert len(writer._pool) <= 3
    finally:
        writer.close()


# ---------------------------------------------------------------------------- sink contract
class FakeEncoder:
    def __init__(self):
        self.healthy = True
        self.codec = "libx264"
        self.generation = 1
        self.restarts = 0
        self.last_error = None
        self.calls = []

    @property
    def stream_info(self):
        return "info"

    def encode(self, pts, image, chunks):
        self.calls.append((pts, len(chunks)))
        if not self.healthy:
            return None
        packet = EncodedPacket(KIND_VIDEO, b"x", len(self.calls) - 1, len(self.calls) - 1, 1, True)
        return EncodeResult(1, len(self.calls) - 1, pts, (packet,), 0.004)

    def close(self):
        self.healthy = False


def make_egress():
    bus = InProcessBus()
    destinations = DestinationSet([], bus=bus)
    return Egress(FakeEncoder(), destinations), bus


def test_egress_publishes_an_encode_timing_per_frame_and_reports_encoder_down():
    egress, bus = make_egress()
    assert egress.write(1.0, frame(1), [(1.0, pcm(1))]) is True
    timings = bus.of(Topic.TIMING)
    assert [t.stage for t in timings] == ["encode"] and timings[0].t_end >= timings[0].t_start
    egress.encoder.healthy = False
    assert egress.write(2.0, frame(2), [(2.0, pcm(2))]) is False  # recovery: the writer drops the slot
    assert egress.dropped_encoder_down == 1
    egress.encoder_event("encoder_down")
    event = bus.of(Topic.SAFETY_EVENT)[-1]
    assert (event.modality.value, event.category.value, event.detail_code) == (
        "system",
        "system_status",
        "encoder_down",
    )


def test_the_sink_implements_the_frame_sink_protocol_and_tracks_encoder_health():
    egress, _ = make_egress()
    sink = EgressFrameSink(egress, canvas=(SHAPE[1], SHAPE[0]))
    try:
        assert isinstance(sink, FrameSink)
        assert sink.healthy is True
        sink.write(0.0, frame(1), pcm(1))
        assert wait_until(lambda: sink.stats()["written"] == 1)
        egress.encoder.healthy = False
        assert sink.healthy is False  # healthy is false while the encoder is dead
        egress.encoder.healthy = True
    finally:
        sink.close()
    assert sink.healthy is False
    sink.write(1.0, frame(2), pcm(2))  # a write after close is ignored, never an error for the engine tick
    sink.close()


def test_the_null_sink_still_counts():
    null = NullFrameSink()
    null.write(0.0, None, pcm(1))
    null.write(0.0, None, pcm(1))
    assert null.frames == 2 and null.pts_nonmonotonic == 1 and null.audio_samples == 3200
    null.close()
    assert null.healthy is False
