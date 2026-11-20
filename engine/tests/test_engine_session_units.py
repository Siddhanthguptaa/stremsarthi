"""Session building blocks and edge cases on the CPU: audio ring, 16 kHz tap, mutes, retro audio,
config changes, request routing, sink health, failures, PTS reset, threads."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from contracts import (
    Category,
    Config,
    ConfigChange,
    Frame,
    HoldCommand,
    HoldSource,
    Lane,
    MaskKind,
    MaskRequest,
    MaskStyle,
    MuteSpan,
    Quad,
    Topic,
)
from engine.bus import LocalBus
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion
from engine.session import AudioRing, Downsampler, Session
from engine.session_fakes import FakeAudioHost, FakeLaneHost, RecordingSink, SyntheticSource, card_detector
from engine.testing import lane_result, region_request

T0, FPS, D = 10.0, 30, 3.0
RATE = 48000
CARD = Quad.from_xyxy(400, 200, 880, 480)


def cfg(**kw) -> Config:
    return Config(_env_file=None, delay_seconds=D, **kw)


def frame(seq: int, *, pts: float | None = None, t0: float = T0, image=None) -> Frame:
    p = seq / FPS if pts is None else pts
    img = np.full((720, 1280, 3), 90, np.uint8) if image is None else image
    return Frame(seq, p, t0 + p, img)


def make_session(*, lanes=(Lane.FAST,), host=None, audio=None, bus=None, sink=None, config=None, clock=None):
    config = config or cfg()
    now = [T0]
    sink = sink or RecordingSink(clock=lambda: now[0])
    engine = DeadlineEngine.from_config(config, lanes, motion=IdentityMotion())
    session = Session(
        config,
        SyntheticSource(1),
        sink,
        lane_host=host,
        audio_host=audio,
        bus=bus,
        engine=engine,
        clock=clock or (lambda: now[0]),
    )
    return session, sink, now


def run_frames(session, now, n, *, results=True, start=0):
    """Ingest ``n`` frames from ``start`` on time with empty FAST heartbeats; step at each arrival."""
    for seq in range(start, start + n):
        t = T0 + seq / FPS
        now[0] = t
        session.on_frame(frame(seq), now=t)
        if results:
            session.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        session.step(t)


def drain(session, now, until):
    t = now[0]
    while t < until:
        t += 0.005
        now[0] = t
        session.step(t)


def tone(n: int, first: int = 0, amp: int = 10000) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * 440.0 * np.arange(first, first + n) / RATE)).astype(np.int16)


# ------------------------------------------------------------------ audio ring


def test_ring_reads_what_was_written_and_silence_for_the_rest():
    ring = AudioRing(1.0)
    ring.write(1000, tone(2000, 1000))
    out = ring.read(500, 3000)
    assert not out[:500].any() and not out[2500:].any()  # never written: silence
    assert np.array_equal(out[500:2500], tone(2000, 1000))


def test_ring_overwritten_samples_read_as_silence_and_old_writes_are_rejected():
    ring = AudioRing(1.0)  # 48000 samples
    ring.write(0, tone(48000, 0))
    ring.write(48000, tone(24000, 48000))  # overwrites the first half second
    assert not ring.read(0, 1000).any()  # gone, not wrong audio
    assert np.array_equal(ring.read(60000, 100), tone(100, 60000))
    ring.write(100, tone(100, 100))  # a second behind the newest: rejected
    assert ring.rejected == 100 and not ring.read(100, 100).any()


def test_ring_zero_and_clear():
    ring = AudioRing(2.0)
    ring.write(0, tone(9600))
    ring.zero(2000, 4000)
    out = ring.read(0, 9600)
    assert not out[2000:4000].any() and out[1999] == tone(2000)[1999]
    assert np.array_equal(out[4000:], tone(9600)[4000:])
    ring.clear()
    assert not ring.read(0, 9600).any() and ring.nbytes == 2 * 48000 * (2 + 8)


def test_downsampler_tracks_a_tone_and_is_block_size_independent():
    x = tone(48000, 0, 12000)
    whole = Downsampler().process(x)
    assert len(whole) == 16000
    peak = np.abs(whole[2000:]).max()
    assert 0.95 * 12000 < peak < 1.05 * 12000
    d = Downsampler()
    parts = [d.process(x[a:b]) for a, b in ((0, 1000), (1000, 1001), (1001, 20000), (20000, 48000))]
    assert np.array_equal(np.concatenate(parts), whole)


def test_audio_is_tapped_to_the_audio_host_at_16khz():
    host = FakeAudioHost()
    session, _, _ = make_session(audio=host)
    for k in range(5):
        session.on_audio(k * 960, tone(960, k * 960))
    assert [c.n_samples for c in host.chunks] == [320] * 5
    assert [c.seq for c in host.chunks] == [0, 1, 2, 3, 4]
    assert host.chunks[2].sample_rate == 16000 and abs(host.chunks[2].pts_start - 1920 / RATE) < 1e-12
    assert host.chunks[0].duration == pytest.approx(0.02)


def test_audio_host_failure_never_breaks_ingest():
    class Broken(FakeAudioHost):
        def feed(self, chunk):
            raise RuntimeError("boom")

    session, _, _ = make_session(audio=Broken())
    session.on_audio(0, tone(960))
    assert session.counts["audio_errors"] == 1
    assert np.array_equal(session.ring.read(0, 960), tone(960))  # still recorded for the output


# ------------------------------------------------------------------ audio slices


def test_slices_are_contiguous_1600_samples_and_in_sync():
    session, sink, now = make_session()
    session.on_audio(0, tone(RATE * 2))
    run_frames(session, now, 40)
    drain(session, now, T0 + D + 1.3)
    audio = np.concatenate([r.audio for r in sink.records])
    assert all(len(r.audio) == 1600 for r in sink.records)
    assert np.array_equal(audio, tone(len(audio)))  # no gap, no overlap, no shift
    assert session.counts["audio_resyncs"] == 0


def test_small_pts_jitter_does_not_resync_or_change_slice_length():
    """RTMP timestamps are whole milliseconds: frame pts wobbles by up to 1 ms."""
    session, sink, now = make_session()
    session.on_audio(0, tone(RATE * 2))
    for seq in range(30):
        pts = seq / FPS + (0.0005 if seq % 2 else -0.0004)
        t = T0 + seq / FPS
        now[0] = t
        session.on_frame(frame(seq, pts=max(0.0, pts)), now=t)
        session.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        session.step(t)
    drain(session, now, T0 + D + 1.2)
    assert {len(r.audio) for r in sink.records} == {1600}
    assert session.counts["audio_resyncs"] == 0


def test_audio_resyncs_to_the_video_clock_after_a_jump():
    session, sink, now = make_session()
    session.on_audio(0, tone(RATE * 4))
    run_frames(session, now, 20)
    # video skips 0.5 s (a dropped stretch): pts jumps, the audio cursor must follow
    t = T0 + 20 / FPS
    for seq in range(20, 40):
        p = (seq + 15) / FPS
        now[0] = T0 + seq / FPS
        session.on_frame(frame(seq, pts=p), now=now[0])
        session.engine.submit_result(lane_result(Lane.FAST, seq, now[0] + 0.01))
        session.step(now[0])
    drain(session, now, T0 + D + 2.0)
    assert session.counts["audio_resyncs"] >= 1 and t > 0
    last = sink.records[-1]
    assert last.audio.any()  # still reading real samples from the shifted position


def test_silence_for_audio_that_never_arrived():
    session, sink, now = make_session()
    run_frames(session, now, 10)
    drain(session, now, T0 + D + 0.5)
    assert sink.records and not any(r.audio.any() for r in sink.records)


def test_mute_span_arriving_just_before_its_start_is_sample_accurate():
    """T3.4-style: a span reaches the engine D-0.2 s before the audio it covers is emitted."""
    bus = LocalBus()
    session, sink, now = make_session(bus=bus)
    session.on_audio(0, tone(RATE * 5))
    span = MuteSpan("m1", 2.0, 2.5, Category.SPOKEN_PHONE, "segment", 0.9, "digits", 0.0)
    sent = []

    for seq in range(150):
        t = T0 + seq / FPS
        now[0] = t
        if t >= T0 + 2.0 + D - 0.2 and not sent:  # 0.2 s before the span's first sample is due
            bus.publish(Topic.MUTE_SPAN, span)
            sent.append(t)
        session.on_frame(frame(seq), now=t)
        session.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        session.step(t)
    drain(session, now, T0 + D + 5.2)
    audio = np.concatenate([r.audio for r in sink.records])
    first, last = round(2.0 * RATE), round(2.5 * RATE)
    original = tone(len(audio))
    assert np.array_equal(audio[:first], original[:first])
    assert not audio[first + 480 : last - 480].any()
    assert np.array_equal(audio[last : 5 * RATE], original[last : 5 * RATE])  # the tone ends at 5 s
    assert session.mutes.late == 0 and session.mutes.accepted == 1


def test_a_span_for_audio_already_emitted_is_dropped_and_counted():
    bus = LocalBus()
    session, sink, now = make_session(bus=bus)
    session.on_audio(0, tone(RATE * 3))
    run_frames(session, now, 40)
    drain(session, now, T0 + D + 1.0)
    bus.publish(Topic.MUTE_SPAN, MuteSpan("late", 0.1, 0.4, Category.SPOKEN_PHONE, "segment", 0.9, "x", 0.0))
    assert session.mutes.late == 1 and session.stats["late_spans"] == 1


def test_hold_silences_audio_fully_and_retro_hold_zeroes_the_ring():
    session, sink, now = make_session()
    session.on_audio(0, tone(RATE * 4))
    run_frames(session, now, 30)  # 1 s of video buffered, nothing emitted yet (D = 3)
    now[0] = T0 + 1.0
    session.hold(HoldSource.HOTKEY, True, now=T0 + 1.0)  # retro 3.0 s: reaches back to T0 - 2.0
    ring_view = session.ring.read(0, 48000)
    assert not ring_view.any()  # the whole buffered second was zeroed
    assert np.array_equal(session.ring.read(48000, 100), tone(100, 48000))  # later audio is untouched
    session.hold(HoldSource.HOTKEY, False, now=T0 + 1.1)


def test_auto_sources_do_not_zero_the_ring():
    session, _, now = make_session()
    session.on_audio(0, tone(RATE))
    run_frames(session, now, 20)
    session.hold(HoldSource.AUTO_TRANSPORT, True, now=T0 + 0.7)
    assert np.array_equal(session.ring.read(0, 100), tone(100))


# ------------------------------------------------------------------ routing and config


def test_audio_originated_requests_are_routed_to_the_engine_and_region_ones_refused():
    bus = LocalBus()
    session, sink, now = make_session(bus=bus)
    full = MaskRequest(
        "ff",
        "audio",
        Category.SPOKEN_PHONE,
        MaskKind.FULL_FRAME,
        (),
        0.0,
        0.5,
        1.5,
        MaskStyle.SOLID,
        0.9,
        "x",
        0.0,
    )
    mouth = MaskRequest(
        "mo",
        "audio",
        Category.SPOKEN_PHONE,
        MaskKind.MOUTH_OF_SPEAKER,
        (),
        0.0,
        0.5,
        1.5,
        MaskStyle.BLUR,
        0.9,
        "x",
        0.0,
    )
    bus.publish(Topic.MASK_REQUEST, full)
    bus.publish(Topic.MASK_REQUEST, mouth)
    region = region_request("rr", (1, 1, 50, 50))
    bus.publish(Topic.MASK_REQUEST, region)  # REGION requests may only come through a LaneResult
    assert session.counts["rejected_requests"] == 1
    probes = []
    for seq in range(60):
        t = T0 + seq / FPS
        now[0] = t
        session.on_frame(frame(seq), now=t)
        session.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        session.step(t)
    drain(session, now, T0 + D + 2.1)
    for rec in sink.records:
        probes.append(rec.pts)
    solid = [i for i, rec in enumerate(sink.records) if rec.pts >= 0.5 and rec.pts <= 1.5]
    assert solid and session.compositor.stats["collapsed"] >= len(solid)  # FULL_FRAME collapsed those frames


def test_config_changes_apply_the_keys_the_engine_owns():
    bus = LocalBus()
    session, _, _ = make_session(bus=bus)
    bus.publish(Topic.CONFIG, ConfigChange({"hold.retro_seconds": 9.0}, "api"))
    assert session.engine.control.retro_seconds == D - 0.5  # clamped to D - 0.5
    bus.publish(Topic.CONFIG, ConfigChange({"hold.retro_seconds": 1.0}, "api"))
    assert session.engine.control.retro_seconds == 1.0
    bus.publish(Topic.CONFIG, ConfigChange({"engine.max_mask_area_ratio": 0.3}, "api"))
    assert session.compositor.max_area_ratio == 0.3
    bus.publish(Topic.CONFIG, ConfigChange({"categories.face.style": "solid"}, "api"))
    assert session.compositor.style_overrides[Category.FACE] is MaskStyle.SOLID
    bus.publish(
        Topic.CONFIG,
        ConfigChange({"categories.nonsense.style": "solid", "engine.max_mask_area_ratio": 5}, "api"),
    )
    assert session.counts["config_ignored"] == 2 and session.compositor.max_area_ratio == 0.3
    bus.publish(
        Topic.CONFIG, ConfigChange({"audio.mute_mode": "bleep", "captions.enabled": True}, "api")
    )  # not ours: no error
    assert session.counts["config_ignored"] == 2


def test_allowlist_version_is_tracked_for_the_status():
    from contracts import AllowlistUpdate

    bus = LocalBus()
    session, _, _ = make_session(bus=bus)
    bus.publish(Topic.ALLOWLIST, AllowlistUpdate("snapshot", None, None, None, 4))
    assert session.status.allowlist_version == 4


def test_hold_command_through_the_bus_and_held_event_on_release():
    bus = LocalBus()
    session, _, now = make_session(bus=bus)
    sub = bus.subscribe([Topic.SAFETY_EVENT])
    now[0] = 50.0
    bus.publish(Topic.HOLD, HoldCommand(HoldSource.APP, True, "x"))
    assert session.engine.hold_active
    now[0] = 52.0
    bus.publish(Topic.HOLD, HoldCommand(HoldSource.APP, False, "x"))
    held = [e for _, e in sub.drain() if e.action == "held"]
    assert len(held) == 1 and held[0].duration_ms == 2000 and held[0].source == "app"


# ------------------------------------------------------------------ failure handling


def test_unhealthy_sink_holds_egress_and_release_follows_recovery():
    session, sink, now = make_session()
    run_frames(session, now, 10)
    sink.healthy = False
    session.step(now[0])
    assert session.engine.control.snapshot().hold_sources == (HoldSource.AUTO_EGRESS,)
    sink.healthy = True
    session.step(now[0])
    assert not session.engine.hold_active


def test_sink_write_errors_are_counted_and_do_not_stop_the_clock():
    class Flaky(RecordingSink):
        def write(self, pts, image, audio):
            if round(pts * FPS) == 3:
                raise OSError("pipe closed")
            super().write(pts, image, audio)

    session, sink, now = make_session(sink=Flaky())
    run_frames(session, now, 8)
    drain(session, now, T0 + D + 0.5)
    # idle input still emits (gap) frames, so count against what was emitted
    assert session.counts["sink_errors"] == 1 and len(sink.records) == session.counts["emitted"] - 1 > 7


def test_a_compose_failure_emits_the_failure_slate_never_raw_pixels(monkeypatch):
    session, sink, now = make_session(sink=RecordingSink(keep_images=True))
    real = session.compositor.compose
    calls = []

    def flaky(decision, highwater=-1):
        calls.append(decision.seq)
        if decision.seq == 2:
            raise RuntimeError("bug")
        return real(decision, highwater)

    monkeypatch.setattr(session.compositor, "compose", flaky)
    run_frames(session, now, 6)
    drain(session, now, T0 + D + 0.4)
    assert session.counts["compose_errors"] == 1
    failure = session.compositor.presenter.store.get("FAILURE")
    assert np.array_equal(sink.records[2].image, failure)


def test_bad_frames_are_counted_and_become_gaps():
    session, sink, now = make_session()
    assert not session.on_frame(Frame(0, 0.0, T0, np.zeros((10, 10, 3), np.uint8)), now=T0)
    assert not session.on_frame(Frame(0, 0.0, T0, np.zeros((720, 1280, 3), np.float32)), now=T0)
    assert session.counts["bad_frames"] == 2


def test_lane_host_errors_are_counted():
    class Bad(FakeLaneHost):
        def poll(self):
            raise RuntimeError("x")

        def submit(self, f):
            raise RuntimeError("y")

    session, _, now = make_session(host=Bad())
    session.on_frame(frame(0), now=T0)
    session.step(T0)
    assert session.counts["lane_errors"] == 2


def test_lanes_follow_the_host():
    host = FakeLaneHost()
    session, _, now = make_session(lanes=host.active_lanes, host=host)
    assert session.engine.lanes == (Lane.FAST,)
    host.active_lanes = frozenset({Lane.FAST, Lane.ACCURATE})
    now[0] = T0 + 5.0
    session.step(now[0])
    assert set(session.engine.lanes) == {Lane.FAST, Lane.ACCURATE}


def test_pts_reset_starts_a_new_clock_and_clears_old_state():
    session, sink, now = make_session()
    session.on_audio(0, tone(RATE * 2))
    run_frames(session, now, 40)
    session.compositor.presenter.blocked_through = 15
    # the source restarts: pts back to 0, sequence numbers keep increasing
    t = T0 + 5.0
    now[0] = t
    session.on_frame(frame(100, pts=0.0, t0=t), now=t)
    assert session.engine.resets == 1
    assert session.compositor.presenter.blocked_through == -1
    assert session.engine.control.state == "PROTECT"  # a reset session is unprotected until covered
    assert not session.ring.read(0, 960).any()  # old samples are not replayed under the new clock


# ------------------------------------------------------------------ threads


def test_threaded_session_runs_and_stops_cleanly():
    """Real threads and real time: 1.2 s of video through a 3.0 s delay needs about 4.5 s."""
    config = cfg()
    start = time.monotonic() + 0.05
    source = SyntheticSource(36, FPS, start=start, realtime=True, beep_at=0.2)
    host = FakeLaneHost(card_detector(36, CARD), latency_s=0.02)
    # the fake host stamps completion in virtual time: frame.arrival is real here, so this just works
    sink = RecordingSink()
    audio = FakeAudioHost()
    bus = LocalBus()
    session = Session(config, source, sink, lane_host=host, audio_host=audio, bus=bus)
    session.engine.close()
    session.engine = DeadlineEngine.from_config(config, host.active_lanes, motion=IdentityMotion())
    host.start()
    session.start()
    try:
        assert session.finished.wait(timeout=12.0), session.stats
    finally:
        session.stop()
    assert len(sink.records) == 36 and session.counts["LIVE"] == 36
    assert min(session.emit_lag) >= 0
    assert audio.chunks and all(c.n_samples == 320 for c in audio.chunks)
    assert not any(t.is_alive() for t in threading.enumerate() if t.name.startswith("engine-"))
    assert bus.stats["published"].get("engine.status", 0) >= 1
    assert np.concatenate([r.audio for r in sink.records]).any()  # the beep got through
    session.stop()  # idempotent
