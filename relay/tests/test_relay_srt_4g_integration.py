"""Loopback SRT with the 4G settings and induced ingest stalls (real MediaMTX v1.21.4 and FFmpeg, CPU only).

Setup: relay SRT latency 400 ms (the 4G preset), AES passphrase on the camera path, transport hold
after 1.0 s. The publisher is frozen with SIGSTOP (a phone in a dead zone: the socket stays open,
no packets flow) and resumed with SIGCONT. Whole file runs in about 30 s. A real ``tc netem`` run is
``scripts/netem_4g.sh`` (manual; needs root network control).
"""

from __future__ import annotations

import os
import signal
import time

import pytest

from contracts.enums import HoldSource, Topic
from relay.ingest import NetworkInput
from relay.mediamtx import MediaMTXSupervisor, verified_binary
from relay.srt import generate_passphrase, resolve_latency_ms
from relay.tests.support_media import (
    Collector,
    StampedPublisher,
    TimedBus,
    free_address,
    have_ffmpeg,
    publish_testsrc,
    stop_process,
    wait_for,
)

HOLD_AFTER_S = 1.0
LATENCY_MS = resolve_latency_ms("4g")


@pytest.fixture(scope="module")
def secured():
    if not have_ffmpeg():
        pytest.skip("ffmpeg not on PATH")
    try:
        binary = verified_binary()
    except Exception as error:  # BinaryVerificationError: binary missing or hash mismatch
        pytest.skip(f"MediaMTX binary unavailable: {error}")
    supervisor = MediaMTXSupervisor(
        free_address(), binary=binary, srt_latency_ms=LATENCY_MS, srt_passphrase=generate_passphrase()
    )
    supervisor.start()
    try:
        yield supervisor
    finally:
        supervisor.stop()


def _freeze(publisher: StampedPublisher) -> float:
    publisher._proc.send_signal(signal.SIGSTOP)
    return time.monotonic()


def _thaw(publisher: StampedPublisher) -> float:
    publisher._proc.send_signal(signal.SIGCONT)
    return time.monotonic()


def test_induced_stalls_hold_within_the_deadline_and_recover_without_a_reconnect(secured):
    bus = TimedBus()
    source = NetworkInput(secured.reader_url(), bus=bus, hold_after_s=HOLD_AFTER_S)
    collector = Collector(source)
    publisher = StampedPublisher(secured.publish_url("srt"), "srt")
    try:
        assert wait_for(lambda: len(collector.frames) > 90, 15), "no stream arrived over encrypted SRT"
        assert not bus.of(Topic.HOLD), "no false hold while joining"

        # 1) short stall (0.5 s, under hold_after_s): must NOT hold
        _freeze(publisher)
        time.sleep(0.5)
        _thaw(publisher)
        before = len(collector.frames)
        assert wait_for(lambda: len(collector.frames) > before + 30, 8), "stream did not resume after 0.5 s"
        assert not bus.of(Topic.HOLD), "a 0.5 s stall is inside the 1.0 s budget and must not hold"

        # 2) long stall (2.5 s): hold within hold_after_s (+ the SRT window of media already in flight)
        stopped_at = _freeze(publisher)
        assert wait_for(lambda: bus.of(Topic.HOLD), 4), "transport hold was never asserted"
        hold_t, hold = bus.times_of(Topic.HOLD)[0]
        assert hold.source == HoldSource.AUTO_TRANSPORT and hold.active is True
        hold_delay = hold_t - stopped_at
        assert hold_delay <= HOLD_AFTER_S + LATENCY_MS / 1000 + 0.3, f"hold took {hold_delay:.2f} s"
        time.sleep(max(0.0, 2.5 - (time.monotonic() - stopped_at)))
        assert source.hold_active, "hold stays asserted for the whole stall"
        resumed_at = _thaw(publisher)
        assert wait_for(lambda: any(not h.active for _, h in bus.times_of(Topic.HOLD)), 8), (
            "hold never released"
        )
        release_t = next(t for t, h in bus.times_of(Topic.HOLD) if not h.active)
        recovery = release_t - resumed_at
        assert recovery <= 3.0, f"recovery took {recovery:.2f} s"
        assert not source.hold_active
        assert source.generation == 1, "the SRT link survived: no reconnect, so no new generation"
        print(
            f"[srt 4g latency={LATENCY_MS} ms, hold_after={HOLD_AFTER_S} s] "
            f"hold {hold_delay:.2f} s after the freeze, released {recovery:.2f} s after the thaw"
        )
    finally:
        try:
            publisher._proc.send_signal(signal.SIGCONT)
        except OSError:
            pass
        collector.stop()
        publisher.stop()


def test_publisher_without_the_passphrase_or_with_a_wrong_one_is_refused(secured):
    good = secured.publish_url("srt")
    assert "passphrase=" in good
    no_pass = good.split("&passphrase=")[0]
    wrong = no_pass + "&passphrase=" + generate_passphrase() + "&pbkeylen=16"
    for url in (no_pass, wrong):
        publisher = publish_testsrc(url, "srt", seconds=4)
        try:
            code = publisher.wait(15)
        finally:
            stop_process(publisher, kill=True)
        assert code != 0, "MediaMTX must refuse an SRT publisher that cannot prove the passphrase"
    assert not secured.publisher_ready(), "nothing was published"


def test_mediamtx_event_hold_fires_before_the_watchdog_on_a_clean_disconnect(secured):
    """A graceful publisher exit makes MediaMTX log a disconnect; ``notify_transport_event`` turns it into a hold."""
    bus = TimedBus()
    source = NetworkInput(secured.reader_url(), bus=bus, hold_after_s=30.0)  # watchdog effectively off
    secured.classifier._on_event = lambda e: source.notify_transport_event(str(e["code"]))
    publisher = StampedPublisher(secured.publish_url("srt"), "srt")
    collector = Collector(source)
    try:
        assert wait_for(lambda: len(collector.frames) > 60, 15)
        started = time.monotonic()
        os.kill(publisher._proc.pid, signal.SIGTERM)  # graceful: SRT shutdown reaches MediaMTX
        assert wait_for(lambda: bus.of(Topic.HOLD), 6), "no hold from the MediaMTX disconnect event"
        delay = bus.times_of(Topic.HOLD)[0][0] - started
        print(f"[srt] MediaMTX disconnect event -> hold after {delay:.2f} s (watchdog was set to 30 s)")
        assert source.stats()["transport_event_publisher_disconnected"] >= 1
    finally:
        secured.classifier._on_event = None
        collector.stop()
        publisher.stop()
