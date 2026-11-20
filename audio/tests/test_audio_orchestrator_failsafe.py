"""T3.2 reviewer blocker: audio must never leave unmuted without a fail-safe span (docs/specs/audio.md 5.3)."""

from __future__ import annotations

import asyncio
import gc
import logging
import threading

import numpy as np
import pytest

from audio.orchestrator import MAX_PENDING_CHUNKS, AudioOrchestrator
from audio.orchestrator.failsafe import Coverage
from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttRace
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segmenter
from audio.testing import ScriptedSTT, Timeline
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import Config
from contracts.detection import MaskRequest, MuteSpan
from contracts.enums import Category, MaskKind, Topic
from contracts.fakes import InProcessBus
from contracts.media import AudioChunk


def chunk(seq: int = 3, pts: float = 1.0, rate: int = 16_000, n: int = 320) -> AudioChunk:
    return AudioChunk(seq, pts, rate, np.zeros(n, dtype=np.int16), 0.0)


def make_pipeline(bus: InProcessBus, config: Config, providers: dict | None = None) -> AudioPipeline:
    seg = Segmenter(StreamingVad(EnergyFrameModel(), config.audio.vad), overlap_s=config.audio.overlap_s)
    race = SttRace(providers or {"local": ScriptedSTT(Timeline(), "local")}, order=("local",))
    return AudioPipeline(config, bus, seg, race, SpokenPiiDetector(SpokenRules()))


def spans(bus: InProcessBus) -> list[MuteSpan]:
    return [e for t, e in bus.history if t is Topic.MUTE_SPAN]


def masks(bus: InProcessBus) -> list[MaskRequest]:
    return [e for t, e in bus.history if t is Topic.MASK_REQUEST]


def assert_failsafe_for(bus: InProcessBus, ch: AudioChunk, why: str) -> None:
    got = spans(bus)
    assert len(got) == 1
    s = got[0]
    assert s.granularity == "failsafe" and s.category is Category.SPOKEN_UNVERIFIED
    assert s.reason == "failsafe_" + why
    assert s.pts_start == pytest.approx(ch.pts_start) and s.pts_end == pytest.approx(ch.pts_end)
    m = masks(bus)
    assert len(m) == 1 and m[0].kind is MaskKind.MOUTH_OF_SPEAKER
    assert (m[0].pts_start, m[0].pts_end) == (s.pts_start, s.pts_end)


# ---------------------------------------------------------------- 2: no sliver passes
def test_failsafe_coverage_keeps_slivers_but_default_still_trims():
    cov = Coverage()
    cov.add(1.0, 2.0)
    cov.add(3.0, 4.0)
    # 5 ms sliver between a covered interval and the edge of the new span
    assert cov.uncovered(0.995, 3.0, min_len=0.0) == [(0.995, 1.0), (2.0, 3.0)]
    assert cov.uncovered(0.995, 3.0) == [(2.0, 3.0)]  # the default (non-fail-safe) trims < 20 ms
    assert cov.uncovered(2.0, 3.0, min_len=0.0) == [(2.0, 3.0)]


def test_failsafe_segment_span_covers_a_sliver_next_to_an_existing_mute():
    bus = InProcessBus()
    pipe = make_pipeline(bus, Config())
    pipe.coverage.add(0.0, 1.495)
    pipe.failsafe_chunk(chunk(pts=1.49, n=3200), "error")  # [1.49, 1.69]; 5 ms already muted
    s = spans(bus)
    assert [(round(x.pts_start, 3), round(x.pts_end, 3)) for x in s] == [(1.495, 1.69)]
    pipe.coverage.add(1.69, 1.7)
    pipe.coverage.add(1.705, 2.0)
    pipe.failsafe_chunk(chunk(pts=1.69, n=4800), "error")  # [1.69, 1.99]: 5 ms hole at 1.70..1.705
    assert any(abs(x.pts_start - 1.7) < 1e-9 and abs(x.pts_end - 1.705) < 1e-9 for x in spans(bus))


# ---------------------------------------------------------------- 1: every drop path publishes a fail-safe
def test_pipeline_drops_non_16k_chunk_with_a_failsafe_span():
    bus = InProcessBus()
    pipe = make_pipeline(bus, Config())
    ch = chunk(rate=8_000, n=160)
    pipe.on_chunk(ch)
    assert pipe.stats["dropped_chunks"] == 1
    assert_failsafe_for(bus, ch, "bad_rate")


def test_pipeline_chunk_with_unusable_rate_still_gets_a_span():
    bus = InProcessBus()
    pipe = make_pipeline(bus, Config())
    pipe.on_chunk(chunk(rate=0))  # duration would divide by zero
    assert len(spans(bus)) == 1 and spans(bus)[0].granularity == "failsafe"


def test_feed_queue_full_publishes_failsafe_instead_of_dropping():
    bus = InProcessBus()
    orch = AudioOrchestrator(Config(), bus, make_pipeline(bus, Config()))
    loop = asyncio.new_event_loop()  # never run: models a stuck loop
    try:
        orch._loop = loop
        orch._pending = MAX_PENDING_CHUNKS
        ch = chunk()
        orch.feed(ch)
        assert orch.dropped == 1 and orch._pending == MAX_PENDING_CHUNKS
        assert_failsafe_for(bus, ch, "queue_full")
    finally:
        loop.close()


def test_feed_when_not_running_publishes_failsafe():
    bus = InProcessBus()
    orch = AudioOrchestrator(Config(), bus, make_pipeline(bus, Config()))
    ch = chunk()
    orch.feed(ch)
    assert_failsafe_for(bus, ch, "not_running")


def test_feed_never_raises_when_the_bus_does():
    class BrokenBus(InProcessBus):
        def publish(self, topic, event):
            raise RuntimeError("bus down")

    orch = AudioOrchestrator(Config(), BrokenBus(), make_pipeline(InProcessBus(), Config()))
    orch.feed(chunk())  # must not raise
    assert orch.failsafe_publish_errors == 1


def test_on_chunk_exception_publishes_failsafe_over_the_chunk():
    bus = InProcessBus()
    pipe = make_pipeline(bus, Config())

    def boom(_chunk):
        raise ValueError("segmenter broke")

    pipe.segmenter.feed_chunk = boom  # type: ignore[method-assign]
    orch = AudioOrchestrator(Config(), bus, pipe)
    orch._pending = 1
    ch = chunk()
    orch._on_chunk(ch)
    assert orch._pending == 0
    assert_failsafe_for(bus, ch, "error")


def test_on_chunk_without_a_pipeline_publishes_failsafe():
    bus = InProcessBus()
    orch = AudioOrchestrator(Config(), bus)
    orch._pending = 1
    ch = chunk()
    orch._on_chunk(ch)
    assert_failsafe_for(bus, ch, "no_pipeline")


def test_failsafe_logs_no_audio_or_text(caplog):
    bus = InProcessBus()
    orch = AudioOrchestrator(Config(), bus)
    orch._pending = 1
    with caplog.at_level(logging.DEBUG):
        orch._on_chunk(chunk(n=64))
    assert "[0" not in caplog.text and "array" not in caplog.text


# ---------------------------------------------------------------- start() must fail loudly
def test_start_raises_when_the_pipeline_cannot_be_built():
    orch = AudioOrchestrator(Config(), InProcessBus())

    def boom():
        raise OSError("model missing")

    orch._build_pipeline = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="failed to build"):
        orch.start()
    assert orch._thread is None and orch._loop is None
    ch = chunk()
    orch.feed(ch)  # after a failed start nothing is discarded silently
    assert len(spans(orch.bus)) == 1


# ---------------------------------------------------------------- 3: warm-up task is reaped on close
class SlowWarm:
    name = "local"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = False

    async def warmup(self) -> None:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def aclose(self) -> None:
        return None


def test_close_cancels_the_warmup_task_without_pending_task_warning(caplog):
    bus = InProcessBus()
    slow = SlowWarm()
    pipe = make_pipeline(bus, Config(), {"local": slow})
    orch = AudioOrchestrator(Config(), bus, pipe)
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        orch.start()
        assert slow.started.wait(5.0)
        warm = orch._warm_task
        orch.close()
        assert slow.cancelled and warm is not None and warm.done()
        del warm, orch, pipe
        gc.collect()
    assert "Task was destroyed but it is pending" not in caplog.text


def test_start_feed_close_round_trip_and_late_feed_is_muted():
    bus = InProcessBus()
    orch = AudioOrchestrator(Config(), bus, make_pipeline(bus, Config()))
    orch.start()
    orch.feed(chunk(pts=0.0))  # silence: passes, no span
    orch.close()
    assert spans(bus) == []
    late = chunk(pts=5.0)
    orch.feed(late)  # after close: fail-safe, not silence
    assert_failsafe_for(bus, late, "not_running")


# ---------------------------------------------------------------- liveness heartbeat (StageTiming on Topic.TIMING)
def beats(bus: InProcessBus) -> list:
    return [e for t, e in bus.history if t is Topic.TIMING and e.stage == "audio_heartbeat"]


def test_heartbeat_about_once_per_second_on_the_virtual_clock_and_stops_after_shutdown():
    from audio.testing import run_virtual

    async def main(loop):
        bus = InProcessBus()
        orch = AudioOrchestrator(Config(), bus, make_pipeline(bus, Config()))
        orch.heartbeat_clock = loop.time
        orch._hb_task = loop.create_task(orch._heartbeat())
        await asyncio.sleep(0.5)
        assert beats(bus) == []
        orch._pending = 1
        orch._on_chunk(chunk(pts=7.0, n=320))  # pts_end = 7.02
        await asyncio.sleep(3.2)  # beats at 1, 2, 3 s
        got = beats(bus)
        assert [b.seq for b in got] == [1, 2, 3]
        assert [round(b.t_start - got[0].t_start, 3) for b in got] == [0.0, 1.0, 2.0]
        assert all(b.t_start == b.t_end for b in got)
        assert got[0].pts is None or got[0].pts == pytest.approx(7.02)
        assert got[-1].pts == pytest.approx(7.02)
        await orch._shutdown()
        n = len(beats(bus))
        await asyncio.sleep(5.0)
        assert len(beats(bus)) == n

    run_virtual(main)


def test_heartbeat_survives_a_broken_bus():
    from audio.testing import run_virtual

    class BrokenBus(InProcessBus):
        def publish(self, topic, event):
            raise RuntimeError("bus down")

    async def main(loop):
        orch = AudioOrchestrator(Config(), BrokenBus(), make_pipeline(InProcessBus(), Config()))
        orch._hb_task = loop.create_task(orch._heartbeat())
        await asyncio.sleep(3.5)  # must not raise or die
        assert not orch._hb_task.done()

    run_virtual(main)


def test_no_heartbeat_after_close_or_after_a_build_failure():
    import time

    bus = InProcessBus()
    orch = AudioOrchestrator(Config(), bus, make_pipeline(bus, Config()))
    orch.heartbeat_s = 0.02
    orch.start()
    deadline = time.monotonic() + 5.0
    while not beats(bus) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert beats(bus)  # the real thread beats while the host runs
    orch.close()
    n = len(beats(bus))
    time.sleep(0.2)
    assert len(beats(bus)) == n

    bus2 = InProcessBus()
    bad = AudioOrchestrator(Config(), bus2)
    bad.heartbeat_s = 0.01

    def boom():
        raise OSError("model missing")

    bad._build_pipeline = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        bad.start()
    time.sleep(0.1)
    assert beats(bus2) == []
