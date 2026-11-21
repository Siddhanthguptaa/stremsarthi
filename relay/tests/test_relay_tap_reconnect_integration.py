"""The tap (``shielded`` on the relay's own MediaMTX) comes back by itself after a sender restart (the engine's
PTS clock restarts at 0) and after its muxer worker dies. Own MediaMTX on free ports (never the demo ports)."""

from __future__ import annotations

import os
import signal
import time

import pytest

from contracts.config import Config
from contracts.fakes.bus import InProcessBus
from relay.runtime import build_egress_sink
from relay.security import PATH_SHIELDED
from relay.tests.support_media import have_ffmpeg, wait_for
from relay.tests.test_relay_egress_integration import FRAMES

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
FPS = 30


def tap_has_both_tracks(mtx) -> bool:
    item = (mtx.paths() or {}).get(PATH_SHIELDED) or {}
    return (
        bool(item.get("ready"))
        and "H264" in item.get("tracks", [])
        and "MPEG-4 Audio" in item.get("tracks", [])
    )


class Feeder:
    """A 30 fps source with continuous audio; ``restart_clock`` makes the PTS start again at 0 (new ingest generation)."""

    def __init__(self, sink) -> None:
        self.sink, self.i, self.offset = sink, 0, 0
        self.began = time.perf_counter()

    def restart_clock(self) -> None:
        self.offset = self.i

    def run_until(self, condition, timeout: float) -> bool:
        import numpy as np

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            phase = np.arange(self.i * 1600, (self.i + 1) * 1600)
            samples = (np.sin(phase * 2 * np.pi * 440 / 48000) * 6000).astype(np.int16)
            self.sink.write((self.i - self.offset) / FPS, FRAMES[self.i % len(FRAMES)], samples)
            self.i += 1
            if condition():
                return True
            delay = self.began + self.i / FPS - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        return False


@pytest.fixture
def sink_and_feeder(mtx):
    bus = InProcessBus()
    config = Config(_env_file=None, destinations=[{"kind": "mediamtx_tap"}])
    sink = build_egress_sink(config, bus, tap_url=mtx.address.rtmp(PATH_SHIELDED, mtx.internal), environ={})
    try:
        yield sink, Feeder(sink)
    finally:
        sink.close()


def test_tap_recovers_with_audio_after_the_pts_clock_restarts(mtx, sink_and_feeder):
    sink, feeder = sink_and_feeder
    assert feeder.run_until(lambda: tap_has_both_tracks(mtx), 25), "the tap never came up"
    publisher = sink.egress.destinations.publishers[0]
    feeder.run_until(lambda: False, 3)  # a little steady state
    connects, restarted = publisher.connects, time.monotonic()
    feeder.restart_clock()  # the sender reconnected: the engine PTS clock starts again at 0
    assert feeder.run_until(lambda: publisher.connects > connects and tap_has_both_tracks(mtx), 20)
    recovery = time.monotonic() - restarted
    print(f"tap back with audio {recovery:.1f} s after the PTS restart")
    assert sink.egress.encoder.generation == 2 and publisher.state == "up"
    assert recovery < 15.0
    # and it stays: audio is flowing on the new timeline, not trimmed until the new clock catches up with the old
    feeder.run_until(lambda: False, 12)
    assert tap_has_both_tracks(mtx) and publisher.state == "up"


def test_tap_reconnects_when_its_muxer_worker_dies(mtx, sink_and_feeder):
    sink, feeder = sink_and_feeder
    assert feeder.run_until(lambda: tap_has_both_tracks(mtx), 25), "the tap never came up"
    publisher = sink.egress.destinations.publishers[0]
    connects, pid = publisher.connects, getattr(publisher._current, "pid", None)
    assert pid, "no muxer worker to kill"
    killed = time.monotonic()
    os.kill(pid, signal.SIGKILL)
    assert wait_for(
        lambda: feeder.run_until(lambda: publisher.connects > connects and tap_has_both_tracks(mtx), 1), 25
    )
    print(f"tap back {time.monotonic() - killed:.1f} s after its muxer worker was killed")
    assert publisher.state == "up" and publisher.failures >= 1
