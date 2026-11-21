"""The Topic.CONFIG feed must never leave a stale OFF toggle: a dropped event or a dead feed resets to fail-closed."""

from __future__ import annotations

import asyncio
import logging

from audio.orchestrator.live_config import LiveAudioSettings
from audio.orchestrator.pipeline import AudioPipeline
from audio.orchestrator.race import SttRace
from audio.pii.detector import SpokenPiiDetector
from audio.pii.rules import SpokenRules
from audio.segmenter import Segmenter
from audio.testing import run_virtual
from audio.vad import EnergyFrameModel, StreamingVad
from contracts.config import Config
from contracts.enums import Category, Topic
from contracts.events import ConfigChange
from contracts.fakes import InProcessBus

OFF = ConfigChange({"categories.spoken_phone.enabled": False}, "test")


def make_pipe(loop, bus):
    config = Config()
    seg = Segmenter(StreamingVad(EnergyFrameModel(), config.audio.vad), overlap_s=config.audio.overlap_s)
    race = SttRace({}, hedge_s=0.8, clock=loop.time)
    return AudioPipeline(config, bus, seg, race, SpokenPiiDetector(SpokenRules()), clock=loop.time)


def test_fail_closed_turns_everything_on_and_never_lowers_sensitivity():
    s = LiveAudioSettings()
    s.apply({"categories.spoken_upi.enabled": False, "categories.spoken_phone.sensitivity": 0.1})
    s.apply({"categories.spoken_address.sensitivity": 1.0})
    assert s.fail_closed()
    assert all(s.enabled.values())
    assert s.sensitivity[Category.SPOKEN_PHONE] == 0.5
    assert s.sensitivity[Category.SPOKEN_ADDRESS] == 1.0  # more sensitive stays
    assert not s.fail_closed()


def test_dropped_config_event_resets_toggles_to_fail_closed(caplog):
    caplog.set_level(logging.ERROR, logger="audio.orchestrator")

    async def main(loop):
        bus = InProcessBus(maxsize=1)
        pipe = make_pipe(loop, bus)
        sub = bus.subscribe([Topic.CONFIG])
        task = loop.create_task(pipe.watch_config(sub, poll_s=0.1))
        bus.publish(Topic.CONFIG, OFF)
        await asyncio.sleep(0.2)
        assert pipe.live.enabled[Category.SPOKEN_PHONE] is False  # applied normally
        # two events before the watcher runs: the first is dropped by the 1-slot queue
        bus.publish(Topic.CONFIG, ConfigChange({"categories.spoken_upi.enabled": False}, "test"))
        bus.publish(Topic.CONFIG, ConfigChange({"audio.mute_mode": "bleep"}, "test"))
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return pipe

    pipe = run_virtual(main)
    assert sub_dropped(pipe) >= 1
    assert pipe.live.enabled[Category.SPOKEN_PHONE] is True  # stale OFF is gone
    assert pipe.live.mute_mode == "bleep"  # the event that did arrive is applied
    assert any(r.levelno >= logging.ERROR for r in caplog.records)


def sub_dropped(pipe) -> int:
    return pipe.stats["config_drops"]


class DeadSub:
    """Iterator that fails on the first read."""

    dropped = 0

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise self.exc

    def close(self) -> None:
        self.closed = True


def test_dead_feed_is_restarted_with_backoff_and_resets_to_fail_closed(caplog):
    caplog.set_level(logging.ERROR, logger="audio.orchestrator")

    async def main(loop):
        bus = InProcessBus()
        pipe = make_pipe(loop, bus)
        pipe.live.apply({"categories.spoken_phone.enabled": False})
        dead = DeadSub(RuntimeError("boom"))
        subs = [DeadSub(StopAsyncIteration()), bus.subscribe([Topic.CONFIG])]
        calls: list[float] = []

        def resub():
            calls.append(loop.time())
            s = subs.pop(0)
            return s

        task = loop.create_task(pipe.supervise_config(dead, resub, backoff_s=(0.5, 1.0), poll_s=0.1))
        await asyncio.sleep(0.1)
        assert pipe.live.enabled[Category.SPOKEN_PHONE] is True  # reset as soon as the feed died
        await asyncio.sleep(
            3.0
        )  # first retry after 0.5 s dies again (iterator ended), second after 1.0 s works
        bus.publish(Topic.CONFIG, OFF)
        await asyncio.sleep(0.3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return pipe, dead, calls

    pipe, dead, calls = run_virtual(main)
    assert dead.closed
    assert len(calls) == 2 and calls[1] - calls[0] >= 1.0 - 1e-6  # backoff grows
    assert pipe.stats["config_restarts"] >= 2
    assert pipe.live.enabled[Category.SPOKEN_PHONE] is False  # the restarted feed delivers again
    assert sum(r.levelno >= logging.ERROR for r in caplog.records) >= 2


def test_resubscribe_failure_keeps_retrying_and_never_crashes():
    async def main(loop):
        bus = InProcessBus()
        pipe = make_pipe(loop, bus)
        n = {"i": 0}

        def resub():
            n["i"] += 1
            if n["i"] < 3:
                raise OSError("bus down")
            return bus.subscribe([Topic.CONFIG])

        task = loop.create_task(
            pipe.supervise_config(DeadSub(RuntimeError()), resub, backoff_s=(0.1,), poll_s=0.1)
        )
        await asyncio.sleep(1.0)
        bus.publish(Topic.CONFIG, OFF)
        await asyncio.sleep(0.3)
        alive = not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return pipe, alive

    pipe, alive = run_virtual(main)
    assert alive and pipe.live.enabled[Category.SPOKEN_PHONE] is False
