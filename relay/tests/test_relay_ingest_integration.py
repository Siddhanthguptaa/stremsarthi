"""Integration: real MediaMTX v1.21.4 plus FFmpeg publishers (testsrc2 + sine) over SRT and RTMP.

Needs the pinned MediaMTX binary (``scripts/fetch_tools.sh``) and ffmpeg; skipped otherwise.
Uses free ports, never the production defaults. CPU only.
"""

from __future__ import annotations

import os
import signal
import time

import av
import numpy as np
import pytest

from contracts.config import Config, RelayConfig
from contracts.enums import HoldSource, Topic
from contracts.runtime import FrameSink, FrameSource
from relay.ingest import NetworkInput, letterbox_geometry
from relay.mediamtx import MediaMTXSupervisor, verified_binary
from relay.security import PATH_SHIELDED
from relay.tests.support_media import (
    Collector,
    StampedPublisher,
    TimedBus,
    free_address,
    kill_stamped,
    publish_testsrc,
    read_stamp,
    stop_process,
    wait_for,
)

PROTOCOLS = ["rtmp", "srt"]


def _source(mtx, **kwargs) -> tuple[NetworkInput, TimedBus]:
    bus = TimedBus()
    return NetworkInput(mtx.reader_url(), bus=bus, **kwargs), bus


def _no_hold_while_streaming(bus, frames) -> None:
    """A hold at the very end is the publisher finishing; one before the last frame is a false alarm."""
    last_frame_at = frames[-1].arrival
    early = [t for t, _ in bus.times_of(Topic.HOLD) if t < last_frame_at - 0.5]
    assert not early, "hold asserted while frames were still flowing"


def _tone_hz(block: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(block.astype(np.float64) * np.hanning(len(block))))
    return float(np.argmax(spectrum)) * 48000 / len(block)


def _check_clean_stream(frames, audio, *, warmup=30):
    """Shared assertions: canvas, PTS rebased and strictly monotonic, no gaps, contiguous audio."""
    assert frames, "no frames received"
    assert all(f.image.shape == (720, 1280, 3) for f in frames[:5])
    gen1 = [f for f in frames if f.generation == 1]
    assert gen1[0].pts == 0.0
    pts = [f.pts for f in gen1]
    assert all(b > a for a, b in zip(pts, pts[1:], strict=False)), "PTS must be strictly increasing"
    assert [f.seq for f in frames] == list(range(len(frames))), (
        "seq is gap-free: nothing dropped between source and reader"
    )
    steady = pts[warmup:]
    gaps = [round(b - a, 4) for a, b in zip(steady, steady[1:], strict=False) if b - a > 0.05]
    assert not gaps, f"PTS gaps after warm-up: {gaps}"
    span = steady[-1] - steady[0]
    assert abs(len(steady) - 1 - span * 30) <= 1.5, "frame count must match the elapsed PTS span at 30 fps"
    blocks = [a for a in audio if a.generation == 1]
    assert blocks and blocks[0][0] == 0
    assert [a[0] for a in blocks] == [i * 960 for i in range(len(blocks))], "audio blocks are contiguous"
    return gen1


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_loopback_stream_is_decoded_to_the_canvas_with_audio(mtx, protocol):
    source, bus = _source(mtx)
    collector = Collector(source)
    publisher = publish_testsrc(mtx.publish_url(protocol), protocol, seconds=14)
    try:
        assert wait_for(lambda: len(collector.frames) > 200, 20), "no stream arrived"
        time.sleep(1)
    finally:
        stats = source.stats()
        collector.stop()
        stop_process(publisher)
    gen1 = _check_clean_stream(collector.frames, collector.audio)
    assert stats["frames_dropped_queue"] == 0 and stats["audio_blocks_dropped_queue"] == 0
    assert abs(source.fps - 30) < 0.6 and abs(source.samples_per_frame - 1600) < 40
    tones = [_tone_hz(a[1]) for a in collector.audio[100:140]]
    assert all(abs(t - 440) < 60 for t in tones), "the 440 Hz sine survives AAC and resampling"
    _no_hold_while_streaming(bus, collector.frames)
    assert len(gen1) > 200


@pytest.mark.slow
@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_sixty_seconds_loopback_with_zero_drops(mtx, protocol):
    """Acceptance T0.3 / gate G0: 60 s of 1280x720@30 reaches the reader with 0 dropped frames."""
    source, bus = _source(mtx)
    collector = Collector(source)
    publisher = publish_testsrc(mtx.publish_url(protocol), protocol, seconds=75)
    try:
        # 60 s of media after the first frame (joining takes a few seconds: the reader waits for a keyframe)
        assert wait_for(lambda: collector.frames and collector.frames[-1].pts >= 60.0, 90), (
            "60 s never arrived"
        )
    finally:
        stats = source.stats()
        collector.stop()
        stop_process(publisher)
    gen1 = _check_clean_stream(collector.frames, collector.audio)
    assert len(gen1) >= 1795, f"only {len(gen1)} frames in 60 s"
    assert stats["frames_dropped_queue"] == 0
    assert stats.get("pts_nonmonotonic_dropped", 0) == 0 and stats["generation"] == 1
    assert stats["max_pts_gap_s"] < 0.08
    _no_hold_while_streaming(bus, collector.frames)
    timings = [e for e in bus.of(Topic.TIMING) if e.stage == "ingest_decode"]
    durations = sorted(e.t_end - e.t_start for e in timings)
    p95 = durations[int(len(durations) * 0.95)]
    print(
        f"[{protocol}] frames={len(gen1)} decode p50={durations[len(durations) // 2] * 1000:.1f} ms p95={p95 * 1000:.1f} ms"
    )
    assert p95 < 0.05, "decode plus resize budget is 0.05 s"


@pytest.mark.parametrize(
    ("protocol", "size"),
    [("rtmp", "640x480"), ("srt", "720x1280"), ("rtmp", "1920x1080")],
    ids=["4x3", "9x16", "1080p"],
)
def test_non_16x9_input_is_letterboxed_not_stretched(mtx, protocol, size):
    width, height = (int(v) for v in size.split("x"))
    source, _ = _source(mtx)
    collector = Collector(source)
    publisher = publish_testsrc(mtx.publish_url(protocol), protocol, seconds=6, size=size)
    try:
        assert wait_for(lambda: len(collector.frames) > 20, 15), "no stream arrived"
    finally:
        collector.stop()
        stop_process(publisher)
    image = collector.frames[10].image
    assert image.shape == (720, 1280, 3)
    w, h, x, y = letterbox_geometry(width, height)
    if (w, h) != (1280, 720):
        left, right = image[:, : x - 2], image[:, x + w + 2 :]
        assert max(left.max(), right.max()) <= 8, "padding is black"
    inner = image[y + 4 : y + h - 4, x + 4 : x + w - 4]
    assert inner.mean() > 25, "the picture fills the fitted rectangle"
    assert inner.std() > 30, "and is real content, not a fill"


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_publisher_loss_raises_hold_then_reconnect_recovers_with_a_new_generation(mtx, protocol):
    source, bus = _source(mtx)
    collector = Collector(source)
    first = StampedPublisher(mtx.publish_url(protocol), protocol)
    try:
        assert wait_for(lambda: len(collector.frames) > 180, 20), "no stream arrived"
        assert source.generation == 1 and not bus.of(Topic.HOLD)
        killed_at = time.monotonic()
        kill_stamped(first)  # no goodbye, like a phone losing signal
        assert wait_for(lambda: bus.of(Topic.HOLD), 5), "transport hold was never asserted"
        hold_t, hold = bus.times_of(Topic.HOLD)[0]
        assert hold.source == HoldSource.AUTO_TRANSPORT and hold.active is True
        assert hold_t - killed_at <= 1.5, f"hold took {hold_t - killed_at:.2f} s after the publisher died"
        frames_before = len(collector.frames)
        restarted_at = time.monotonic()
        second = StampedPublisher(mtx.publish_url(protocol), protocol)
        try:
            assert wait_for(lambda: source.generation >= 2 and len(collector.frames) > frames_before + 30, 20)
            releases = [(t, h) for t, h in bus.times_of(Topic.HOLD) if not h.active]
            assert releases and releases[0][1].source == HoldSource.AUTO_TRANSPORT
            recovery = releases[0][0] - restarted_at
            print(
                f"[{protocol}] hold after {hold_t - killed_at:.2f} s, recovered {recovery:.2f} s after the publisher restarted"
            )
            new_gen = [f for f in collector.frames if f.generation >= 2]
            assert new_gen[0].pts == 0.0, "the PTS clock restarts at the new generation"
            assert any(a.generation >= 2 and a[0] == 0 for a in collector.audio), (
                "and so does the audio index"
            )
            assert not source.hold_active
        finally:
            second.stop()
    finally:
        collector.stop()
        first.stop()


def test_mediamtx_crash_is_restarted_with_backoff_and_the_reader_recovers():
    address = free_address()
    supervisor = MediaMTXSupervisor(address, binary=verified_binary()).start()
    source = NetworkInput(supervisor.reader_url())
    collector = Collector(source)
    publisher = publish_testsrc(supervisor.publish_url("rtmp"), "rtmp")
    try:
        assert wait_for(lambda: len(collector.frames) > 180, 20)
        old_pid = supervisor.pid
        os.kill(old_pid, signal.SIGKILL)
        assert wait_for(lambda: supervisor.restart_count == 1, 10), "MediaMTX was not restarted"
        assert supervisor.restart_delays == [0.5]
        assert supervisor.pid != old_pid and supervisor.paths() is not None, "the API answers again"
        stop_process(publisher)
        publisher = publish_testsrc(supervisor.publish_url("rtmp"), "rtmp")
        assert wait_for(lambda: source.generation >= 2, 20), "the reader did not reconnect after the restart"
    finally:
        collector.stop()
        stop_process(publisher)
        supervisor.stop()


def test_ports_already_in_use_fail_before_spawning():
    first = MediaMTXSupervisor(free_address(), binary=verified_binary()).start()
    try:
        second = MediaMTXSupervisor(first.address, binary=verified_binary())
        with pytest.raises(Exception, match="already in use"):
            second.start()
    finally:
        first.stop()


def test_publish_auth_is_enforced_and_logged_as_a_fixed_code(mtx):
    bad = mtx.publish_url("rtmp").replace(mtx.phone.password, "wrong-password")
    publisher = publish_testsrc(bad, "rtmp", seconds=3)
    try:
        assert publisher.wait(20) != 0, "a publisher with the wrong password must be refused"
    finally:
        stop_process(publisher)
    assert wait_for(lambda: mtx.classifier.counts["auth-failure"] >= 1, 5)
    assert mtx.publish_url("rtmp").count(mtx.phone.password) == 1


def test_phone_login_cannot_publish_the_shielded_tap_or_read_the_camera(mtx):
    phone = mtx.phone
    shielded = mtx.address.rtmp(PATH_SHIELDED, phone)
    publisher = publish_testsrc(shielded, "rtmp", seconds=3)
    try:
        assert publisher.wait(20) != 0, "the phone login must not be able to publish `shielded`"
    finally:
        stop_process(publisher)
    with pytest.raises(av.error.FFmpegError):
        av.open(
            mtx.address.rtsp("camera", phone), timeout=(2.0, 2.0), container_options={"rtsp_transport": "tcp"}
        )
    with pytest.raises(av.error.FFmpegError):
        av.open(mtx.address.rtsp("camera"), timeout=(2.0, 2.0), container_options={"rtsp_transport": "tcp"})


def test_internal_login_publishes_the_shielded_tap_and_loopback_reads_it_anonymously(mtx):
    """The `shielded` tap is what the eval gates read: anonymous on loopback, publish only by the relay."""
    import subprocess

    url = mtx.address.rtsp(PATH_SHIELDED, mtx.internal)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30",
    ]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-g",
        "60",
        "-t",
        "6",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        url,
    ]
    pusher = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        assert wait_for(lambda: mtx.publisher_ready(PATH_SHIELDED), 10), "the tap never came up"
        tap = NetworkInput(mtx.tap_url())
        try:
            frame = next(iter(tap.frames()))
            assert frame.image.shape == (720, 1280, 3)
        finally:
            tap.close()
    finally:
        stop_process(pusher)


def test_build_returns_working_endpoints_and_closes_everything():
    """``relay.runtime:build`` starts MediaMTX, returns the reader and the real egress sink."""
    from relay.runtime import build

    address = free_address()
    config = Config(
        _env_file=None,
        ingest_port_rtmp=address.rtmp_port,
        ingest_port_srt=address.srt_port,
        relay=RelayConfig(mediamtx_api_port=address.api_port),
    )
    bus = TimedBus()
    endpoints = build(config, bus, environ={"INGEST_PORT_RTSP": str(address.rtsp_port)})
    assert isinstance(endpoints.source, FrameSource) and isinstance(endpoints.sink, FrameSink)
    publisher = publish_testsrc(endpoints.source.publish_url("rtmp"), "rtmp", seconds=5)
    try:
        frames = endpoints.source.frames()
        first = next(frames)
        assert first.pts == 0.0 and first.image.shape == (720, 1280, 3)
        endpoints.sink.write(first.pts, first.image, np.zeros(1600, np.int16))
        assert endpoints.sink.stats()["submitted"] == 1
    finally:
        endpoints.source.close()
        endpoints.sink.close()
        stop_process(publisher)
    assert endpoints.source.supervisor.process.poll() is not None, "closing the source stops MediaMTX"
    assert endpoints.source.supervisor.paths() is None


def test_build_honours_an_operator_chosen_publish_login():
    from relay.runtime import build

    address = free_address()
    config = Config(
        _env_file=None,
        ingest_port_rtmp=address.rtmp_port,
        ingest_port_srt=address.srt_port,
        relay=RelayConfig(mediamtx_api_port=address.api_port),
    )
    environ = {
        "INGEST_PORT_RTSP": str(address.rtsp_port),
        "INGEST_PUBLISH_USER": "obs",
        "INGEST_PUBLISH_PASSWORD": "chosen-pass-123",
    }
    endpoints = build(config, TimedBus(), environ=environ)
    try:
        assert "user=obs&pass=chosen-pass-123" in endpoints.source.publish_url("rtmp")
    finally:
        endpoints.source.close()


@pytest.mark.slow
@pytest.mark.parametrize(("protocol", "budget_s"), [("rtmp", 0.25), ("srt", 0.30)])
def test_ingest_plus_decode_latency_within_budget(protocol, budget_s):
    """Publisher stamps every frame with a clock; the reader compares at decode. Includes the sender's x264 encode."""
    supervisor = MediaMTXSupervisor(free_address(), binary=verified_binary(), srt_latency_ms=120).start()
    source = NetworkInput(supervisor.reader_url())
    publisher = StampedPublisher(supervisor.publish_url(protocol), protocol)
    latencies: list[float] = []
    try:
        assert wait_for(lambda: supervisor.publisher_ready(), 10)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            frame = source._video.get(0.2)
            if frame is not None:
                stamp = read_stamp(frame.image)
                latencies.append((((int(frame.arrival * 1000) & 0xFFFFFFFF) - stamp) & 0xFFFFFFFF) / 1000.0)
    finally:
        source.close()
        publisher.stop()
        supervisor.stop()
    latencies = sorted(latencies[45:])
    assert len(latencies) > 300
    p50, p95 = latencies[len(latencies) // 2], latencies[int(len(latencies) * 0.95)]
    print(
        f"[{protocol}] ingest+decode latency p50={p50 * 1000:.0f} ms p95={p95 * 1000:.0f} ms max={latencies[-1] * 1000:.0f} ms"
    )
    assert p95 <= budget_s


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.slow
def test_audio_and_video_share_one_clock_across_a_long_run(mtx, protocol):
    """A white flash and a 1 kHz beep start together every 2 s; their rebased times must agree.

    Runs 50 s because FFmpeg's RTSP demuxer re-bases timestamps after about 40 s, which is why the
    relay reads MediaMTX over RTMP; a regression to RTSP would put a flash and its beep seconds apart.
    """
    from relay.tests.support_media import publish_flash_beep

    source, _ = _source(mtx)
    collector = Collector(source, probe=lambda image: bool(image[300:420, 400:880].mean() > 128))
    publisher = publish_flash_beep(mtx.publish_url(protocol), protocol, seconds=50)
    try:
        assert wait_for(lambda: publisher.poll() is not None, 70), "publisher never finished"
        time.sleep(1.5)
    finally:
        collector.stop()
        stop_process(publisher)
    frames = [f for f in collector.frames if f.generation == 1]
    pts = [f.pts for f in frames]
    assert all(b > a for a, b in zip(pts, pts[1:], strict=False))
    assert max(b - a for a, b in zip(pts, pts[1:], strict=False)) < 0.1, "no PTS jump in 50 s"
    bright = [f.probe for f in frames]
    flashes = [frames[i].pts for i in range(1, len(frames)) if bright[i] and not bright[i - 1]]
    loud = [np.abs(a[1]).max() > 8000 for a in collector.audio]
    beeps = [collector.audio[i][0] / 48000 for i in range(1, len(loud)) if loud[i] and not loud[i - 1]]
    assert len(flashes) >= 15 and len(beeps) >= 15
    offsets = []
    for flash in flashes:
        nearest = min(beeps, key=lambda b: abs(b - flash))
        offsets.append(nearest - flash)
    print(
        f"[{protocol}] flash-to-beep offset: median {np.median(offsets) * 1000:.0f} ms, worst {max(map(abs, offsets)) * 1000:.0f} ms over {len(offsets)} events"
    )
    assert max(abs(o) for o in offsets) <= 0.08, "audio and video drift apart on the shared clock"
