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
    def __init__(
        self,
        *,
        watchdog: float | None = 1.5,
        grace: float | None = 1.0,
        audio: bool = True,
        host=None,
        lag: float | None = None,
        **eng,
    ):
        self.config = Config(_env_file=None, delay_seconds=D, **({"engine": eng} if eng else {}))
        self.bus = LocalBus()
        self.now = [T0]
        self.sink = RecordingSink(clock=lambda: self.now[0])
        engine = DeadlineEngine.from_config(self.config, (Lane.FAST,), motion=IdentityMotion())
        self.session = Session(
            self.config,
            SyntheticSource(1),
            self.sink,
            audio_host=(host or FakeAudioHost()) if audio else None,
            bus=self.bus,
            engine=engine,
            clock=lambda: self.now[0],
            audio_watchdog_s=watchdog,
            audio_grace_s=grace,
            audio_heartbeat_lag_s=lag,
        )
        self.sub = self.bus.subscribe([Topic.SAFETY_EVENT, Topic.ENGINE_STATUS])

    def beat(self, t: float, pts: float | None = -1.0) -> None:
        pts = max(0.0, t - T0) if pts == -1.0 else pts  # default: the host has seen the audio up to now
        self.bus.publish(Topic.TIMING, StageTiming("audio_heartbeat", 0, pts, t, t))

    def run(
        self,
        seconds: float,
        *,
        beats: tuple[float, float] | None = None,
        beat_every: float = 0.5,
        beat_pts=None,
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
                self.beat(t, beat_pts(t - T0) if beat_pts else -1.0)
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
    rig.run(
        7.0, beats=(2.0, 20.0)
    )  # first heartbeat 2 s in: inside the grace (4 s requested, clamped to 2.5)
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


# ------------------------------------------------------------------ privacy reviewer fixes (D-46)


def test_default_config_never_lets_unchecked_audio_out_when_the_host_never_starts():
    """Defaults (grace 10 s requested, timeout 3 s) with a 3 s delay: the grace is clamped to delay - 0.5 s and
    emitted audio is muted until a heartbeat vouches for it. Not one sample goes out in 12 s of audio."""
    rig = Rig(watchdog=None, grace=None)
    s = rig.session
    assert s.audio_grace_s == D - 0.5 and s.audio_watchdog_s == D - 0.5
    rig.run(12.0)
    assert not rig.audio().any() and s.audio_unchecked and s.counts["audio_watchdog"] == 1


def test_explicit_long_grace_is_clamped_too():
    rig = Rig(watchdog=1.5, grace=30.0)
    assert rig.session.audio_grace_s == D - 0.5
    rig.run(8.0)
    assert not rig.audio().any()


def test_a_healthy_host_with_default_settings_is_not_muted():
    rig = Rig(watchdog=None, grace=None)
    rig.run(8.0, beats=(0.5, 30.0), beat_every=1.0)  # the real host beats once a second
    n = 8 * RATE
    assert np.array_equal(rig.audio()[:n], tone(n))
    assert rig.session.counts["audio_watchdog"] == 0 and rig.session.counts["audio_unvouched"] == 0


def test_a_late_first_heartbeat_mutes_only_what_was_not_vouched_for():
    rig = Rig(watchdog=None, grace=None)
    rig.run(8.0, beats=(2.0, 30.0), beat_every=1.0)  # inside the clamped 2.5 s grace
    assert rig.session.counts["audio_watchdog"] == 0
    assert np.array_equal(rig.audio()[: 8 * RATE], tone(8 * RATE))


def test_alive_but_stuck_loop_is_muted_from_where_its_pts_stopped():
    """Heartbeats keep coming but their pts froze at 1.0 s: the loop is alive, the audio intake is not."""
    rig = Rig(watchdog=None, grace=None)
    s = rig.session
    rig.run(10.0, beats=(0.0, 30.0), beat_every=0.5, beat_pts=lambda off: min(off, 1.0))
    audio = rig.audio()
    cut = 30 * SLICE  # 1.0 s
    assert np.array_equal(audio[:cut], tone(cut))
    assert not audio[cut : 10 * RATE].any()  # nothing after the frozen pts is ever emitted
    assert s.audio_beats_rejected > 0 and s.counts["audio_unvouched"] > 0
    assert s.audio_unchecked and s.counts["audio_watchdog"] == 1  # and the watchdog reports it


def test_a_lagging_or_pts_less_beat_is_ignored_once_audio_has_advanced():
    rig = Rig(watchdog=None, grace=None)
    s = rig.session
    for seq in range(150):  # 5 s of audio ingested
        s.on_audio(seq * SLICE, tone(SLICE, seq * SLICE))
    newest = 150 * SLICE / RATE
    assert s.audio_heartbeat(now=T0, pts=None) is False  # nothing proves it has seen the audio
    assert s.audio_heartbeat(now=T0, pts=newest - 2.5) is False  # more than the 2 s margin behind
    assert s.audio_heartbeat(now=T0, pts=newest - 1.5) is True
    assert s.audio_beats_rejected == 2 and s.audio_heartbeats == 1


def test_heartbeat_lag_margin_is_configurable():
    rig = Rig(watchdog=None, grace=None, lag=0.5)
    s = rig.session
    for seq in range(60):
        s.on_audio(seq * SLICE, tone(SLICE, seq * SLICE))
    newest = 60 * SLICE / RATE
    assert s.audio_heartbeat(now=T0, pts=newest - 1.0) is False
    assert s.audio_heartbeat(now=T0, pts=newest - 0.3) is True


def test_first_beat_before_any_audio_is_accepted():
    rig = Rig(watchdog=None, grace=None)
    assert rig.session.audio_heartbeat(now=T0, pts=None) is True


class FailingHost(FakeAudioHost):
    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self.fail_on, self.calls = fail_on, 0

    def feed(self, chunk) -> None:
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("feed failed")
        super().feed(chunk)


def test_a_block_that_could_not_be_handed_to_the_audio_host_goes_out_as_silence():
    rig = Rig(watchdog=None, grace=None, host=FailingHost(fail_on=61))
    rig.run(6.0, beats=(0.0, 30.0), beat_every=0.5)
    audio = rig.audio()
    k = 60  # the 61st block: samples [k * SLICE, (k + 1) * SLICE)
    assert rig.session.counts["audio_errors"] == 1
    assert not audio[k * SLICE : (k + 1) * SLICE].any()
    assert np.array_equal(audio[: k * SLICE], tone(k * SLICE))
    assert np.array_equal(audio[(k + 1) * SLICE : 6 * RATE], tone(6 * RATE)[(k + 1) * SLICE :])
