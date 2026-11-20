"""Audio watchdog (no ``audio_heartbeat`` from the audio host means unchecked audio goes out muted), the
``hold_received`` timing event and the engine_delay stage keys. CPU only, virtual clock."""

from __future__ import annotations

import json

import numpy as np

from contracts import Config, HoldCommand, HoldSource, Lane, StageTiming, Topic
from engine.bus import LocalBus
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion
from engine.session import Session
from engine.session_fakes import FakeAudioHost, RecordingSink, SyntheticSource
from engine.testing import lane_result

T0, FPS, D = 10.0, 30, 3.0
RATE = 48000
SLICE = RATE // FPS


def tone(n: int, first: int = 0, amp: int = 10000) -> np.ndarray:
    return (amp * np.sin(2 * np.pi * 440.0 * np.arange(first, first + n) / RATE)).astype(np.int16)


class Rig:
    def __init__(self, *, watchdog: float | None = 1.5, grace: float | None = 1.0, audio: bool = True, **eng):
        self.config = Config(_env_file=None, delay_seconds=D, **({"engine": eng} if eng else {}))
        self.bus = LocalBus()
        self.now = [T0]
        self.sink = RecordingSink(clock=lambda: self.now[0])
        engine = DeadlineEngine.from_config(self.config, (Lane.FAST,), motion=IdentityMotion())
        self.session = Session(
            self.config,
            SyntheticSource(1),
            self.sink,
            audio_host=FakeAudioHost() if audio else None,
            bus=self.bus,
            engine=engine,
            clock=lambda: self.now[0],
            audio_watchdog_s=watchdog,
            audio_grace_s=grace,
        )
        self.sub = self.bus.subscribe([Topic.SAFETY_EVENT, Topic.ENGINE_STATUS])

    def beat(self, t: float) -> None:
        self.bus.publish(Topic.TIMING, StageTiming("audio_heartbeat", 0, None, t, t))

    def run(
        self, seconds: float, *, beats: tuple[float, float] | None = None, beat_every: float = 0.5
    ) -> None:
        """Real-time ingest of video and a 440 Hz tone; heartbeats every ``beat_every`` s inside ``beats``
        (offsets from T0); keeps stepping ``D + 0.5`` s past the end so everything is emitted."""
        s = self.session
        n = int(seconds * FPS)
        last_beat = -1e9
        for seq in range(n + int((D + 0.5) * FPS)):
            t = T0 + seq / FPS
            self.now[0] = t
            if seq < n:
                s.on_audio(seq * SLICE, tone(SLICE, seq * SLICE))
                s.on_frame(_frame(seq), now=t)
                s.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
            if beats and beats[0] <= t - T0 < beats[1] and t - last_beat >= beat_every:
                last_beat = t
                self.beat(t)
            s.step(t)

    def audio(self) -> np.ndarray:
        return np.concatenate([r.audio for r in self.sink.records])

    def events(self):
        return [e for _, e in self.sub.drain()]


def _frame(seq: int):
    from contracts import Frame

    p = seq / FPS
    return Frame(seq, p, T0 + p, np.full((720, 1280, 3), 90, np.uint8))


def failsafe_audio_events(events):
    return [e for e in events if getattr(e, "detail_code", "") == "audio-module-silent"]


def test_heartbeats_keep_the_audio_unmuted():
    rig = Rig()
    rig.run(6.0, beats=(0.0, 20.0))
    audio = rig.audio()
    n = 6 * RATE
    assert np.array_equal(audio[:n], tone(n))
    assert not rig.session.audio_unchecked and rig.session.counts["audio_watchdog"] == 0
    assert not failsafe_audio_events(rig.events())


def test_a_dead_audio_module_mutes_everything_and_reports_it():
    rig = Rig()  # grace 1.0 s, timeout 1.5 s, and the host never answers
    rig.run(6.0)
    s = rig.session
    assert s.audio_unchecked and s.counts["audio_watchdog"] == 1
    assert (
        not rig.audio().any()
    )  # not one unchecked sample went out, including the ones ingested before it fired
    events = rig.events()
    fails = failsafe_audio_events(events)
    assert len(fails) == 1 and fails[0].action == "failsafe" and fails[0].duration_ms is None
    assert fails[0].category.value == "failsafe" and fails[0].modality.value == "audio"
    statuses = [e for e in events if hasattr(e, "lanes_stale")]
    assert statuses and "audio" in statuses[-1].lanes_stale


def test_grace_period_tolerates_a_slow_start_then_heartbeats_take_over():
    rig = Rig(watchdog=1.5, grace=4.0)
    rig.run(7.0, beats=(3.0, 20.0))  # first heartbeat 3 s in: inside the 4 s grace
    assert rig.session.counts["audio_watchdog"] == 0
    n = 7 * RATE
    assert np.array_equal(rig.audio()[:n], tone(n))


def test_audio_is_muted_while_the_module_is_silent_and_passes_again_when_heartbeats_resume():
    rig = Rig(watchdog=1.5, grace=4.0)
    s = rig.session
    # heartbeats for 2 s, a gap of 3 s (dead), then heartbeats again from 5 s
    rig.run(9.0, beats=(0.0, 2.0))
    assert s.audio_unchecked  # the gap fired
    assert s.counts["audio_watchdog"] == 1
    # resume
    t = rig.now[0]
    rig.beat(t + 0.1)
    rig.now[0] = t + 0.2
    s.step(t + 0.2)
    assert not s.audio_unchecked
    rig.now[0] = t + 1.5  # the next status tick
    rig.beat(t + 1.5)
    s.step(t + 1.5)
    events = rig.events()
    fails = failsafe_audio_events(events)
    assert len(fails) == 2 and fails[0].event_id == fails[1].event_id  # one episode: open, then closed
    assert fails[1].duration_ms is not None and fails[1].duration_ms > 0
    statuses = [e for e in events if hasattr(e, "lanes_stale")]
    assert "audio" not in statuses[-1].lanes_stale


def test_recovery_in_flight_audio_after_the_resume_is_not_muted():
    rig = Rig(watchdog=1.0, grace=1.0)
    s = rig.session
    # phase 1: no heartbeat at all for 3 s
    for seq in range(90):
        t = T0 + seq / FPS
        rig.now[0] = t
        s.on_audio(seq * SLICE, tone(SLICE, seq * SLICE))
        s.on_frame(_frame(seq), now=t)
        s.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        s.step(t)
    assert s.audio_unchecked
    # phase 2: heartbeats resume, 3 more seconds of audio
    for seq in range(90, 180):
        t = T0 + seq / FPS
        rig.now[0] = t
        if seq % 15 == 0:
            rig.beat(t)
        s.on_audio(seq * SLICE, tone(SLICE, seq * SLICE))
        s.on_frame(_frame(seq), now=t)
        s.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        s.step(t)
    for seq in range(180, 180 + int((D + 0.5) * FPS)):
        t = T0 + seq / FPS
        rig.now[0] = t
        if seq % 15 == 0:
            rig.beat(t)
        s.step(t)
    assert not s.audio_unchecked
    audio = rig.audio()
    first_ok = 120 * SLICE  # audio ingested after the resume (frame 90 + a beat period of slack)
    assert not audio[: 90 * SLICE].any()  # everything ingested while the module was dead is muted
    assert np.array_equal(audio[first_ok : 170 * SLICE], tone(170 * SLICE)[first_ok:])


def test_watchdog_is_off_without_an_audio_host_or_with_a_zero_timeout():
    rig = Rig(audio=False)
    rig.run(5.0)
    assert not rig.session.audio_unchecked
    assert np.array_equal(rig.audio()[: 5 * RATE], tone(5 * RATE))
    rig = Rig(watchdog=0.0)
    rig.run(5.0)
    assert not rig.session.audio_unchecked and rig.session.counts["audio_watchdog"] == 0


def test_timeout_is_clamped_below_the_delay_so_unchecked_speech_is_still_buffered():
    rig = Rig(watchdog=30.0)
    assert rig.session.audio_watchdog_s == D - 0.5


def test_hold_received_is_logged_with_the_engine_clock_and_engine_delay_keeps_pts(tmp_path):
    path = tmp_path / "engine_timing.jsonl"
    rig = Rig(timing_log_path=str(path))
    s = rig.session
    for seq in range(30):
        t = T0 + seq / FPS
        rig.now[0] = t
        s.on_frame(_frame(seq), now=t)
        s.engine.submit_result(lane_result(Lane.FAST, seq, t + 0.01))
        s.step(t)
    rig.now[0] = T0 + 1.25
    rig.bus.publish(Topic.HOLD, HoldCommand(HoldSource.APP, True, "x"))
    rig.now[0] = T0 + 1.5
    rig.bus.publish(Topic.HOLD, HoldCommand(HoldSource.APP, False, "x"))
    for seq in range(30, 30 + int((D + 0.5) * FPS)):
        rig.now[0] = T0 + seq / FPS
        s.step(rig.now[0])
    s.stop()
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    events = [x for x in lines if x.get("type") == "event" and x["name"] == "hold_received"]
    assert [e["t"] for e in events] == [T0 + 1.25, T0 + 1.5]
    assert [e["detail"] for e in events] == ["on", "off"]
    delays = [x for x in lines if x.get("stage") == "engine_delay"]
    assert delays
    for x in delays:
        assert x["pts"] is not None and x["seq"] == round(x["pts"] * FPS)
