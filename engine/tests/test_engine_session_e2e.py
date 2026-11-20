"""Acceptance T1.1: SPECIMEN-style card + FakeDetector through ``Session`` to a recording sink (CPU, virtual time).

D = 3.0 s (the minimum ``Config`` allows). Frame ``i`` arrives at ``T0 + i/30`` and is due at
``T0 + 3 + i/30``. The test advances a virtual clock in 5 ms ticks, so nothing waits in real time.
"""

from __future__ import annotations

import json

import numpy as np

from contracts import CONTRACT_VERSION, Config, HoldCommand, HoldSource, Quad, Topic
from engine.bus import LocalBus
from engine.control import PROTECT
from engine.deadline import DeadlineEngine
from engine.motion import IdentityMotion
from engine.session import Session
from engine.session_fakes import FakeLaneHost, RecordingSink, SyntheticSource, card_detector

T0 = 10.0
FPS = 30
D = 3.0
CARD = Quad.from_xyxy(400, 200, 880, 480)
INNER = (
    slice(240, 440),
    slice(440, 840),
)  # card interior: rows 240..440, columns 440..840 (stripes live here)
STAMP = (slice(0, 64), slice(0, 64))
TICK = 0.005


def _cfg(**kw) -> Config:
    return Config(_env_file=None, delay_seconds=D, **kw)


class Rig:
    """A session wired to synthetic media, driven in virtual time."""

    def __init__(
        self, n_frames: int, *, config: Config | None = None, host: FakeLaneHost | None = None, **src
    ):
        self.vt = [T0]
        self.config = config or _cfg()
        self.source = SyntheticSource(n_frames, FPS, start=T0, **src)
        self.host = host or FakeLaneHost(card_detector(n_frames, CARD))
        self.bus = LocalBus()
        self.sink = RecordingSink(lambda im: self._probe(im), clock=lambda: self.vt[0])
        self.session = Session(
            self.config,
            self.source,
            self.sink,
            lane_host=self.host,
            bus=self.bus,
            engine=DeadlineEngine.from_config(self.config, self.host.active_lanes, motion=IdentityMotion()),
            clock=lambda: self.vt[0],
        )
        self.base = self.source.card._base()
        self.n = n_frames
        self.reference_slate = {
            k: self.session.compositor.presenter.store.get(k)
            for k in ("HOLD", "PROTECT", "TRANSPORT", "FAILURE")
        }
        self.audio = iter(self.source.audio_blocks())
        self._next_audio = next(self.audio, None)
        self.events: list[tuple[float, str]] = []

    def _probe(self, image: np.ndarray):
        slate = next((k for k, s in self.reference_slate.items() if np.array_equal(image, s)), None)
        return {
            "std": float(image[INNER].std()),
            "stamp": bool(image[STAMP].mean() > 200),
            "slate": slate,
            "flat": bool(image.min() == image.max()) or bool((image.reshape(-1, 3) == image[0, 0]).all()),
            "outside_equal": self._outside_equal(image),
        }

    def _outside_equal(self, image: np.ndarray) -> bool:
        x1, y1, x2, y2 = (int(v) for v in CARD.bbox())
        m = 12 + 12  # pad_px plus the feather and grow margins
        mask = np.ones(image.shape[:2], bool)
        mask[max(0, y1 - m) : y2 + m, max(0, x1 - m) : x2 + m] = False
        return bool(np.array_equal(image[mask], self.base[mask]))

    def feed_audio_until(self, t: float) -> None:
        while self._next_audio is not None and T0 + (self._next_audio[0] + 960) / 48000 <= t:
            self.session.on_audio(*self._next_audio)
            self._next_audio = next(self.audio, None)

    def run(self, *, until: float | None = None, hook=None) -> None:
        """Ingest every frame at its arrival time, then tick until the engine has drained (or ``until``)."""
        frames = self.source.frames()
        pending = next(frames, None)
        end = until if until is not None else T0 + self.n / FPS + D + 0.5
        t, ended = T0, False
        while t <= end and not self.session.finished.is_set():
            self.vt[0] = t
            while pending is not None and pending.arrival <= t + 1e-9:
                self.feed_audio_until(t)
                self.session.on_frame(pending, now=t)
                pending = next(frames, None)
            if hook is not None:
                hook(t)
            self.session.step(t)
            if pending is None and not ended:
                self.session.engine.end()
                ended = True
            t += TICK

    @property
    def probes(self) -> list:
        return [r.probe for r in self.sink.records]


def test_card_masked_in_every_card_visible_frame():
    rig = Rig(90)
    rig.run()
    assert len(rig.sink.records) == 90
    assert all(p["slate"] is None for p in rig.probes), "no slate expected on a healthy run"
    assert rig.session.counts["LIVE"] == 90 and rig.session.counts["PROTECT"] == 0
    # the source card has strong stripes (std ~ 90): after BLUR the interior is nearly flat in every frame
    source_std = float(rig.base[INNER].std())
    assert source_std > 70
    assert max(p["std"] for p in rig.probes) < 12, [round(p["std"], 1) for p in rig.probes][:10]
    # and pixels outside the (padded) quad are bit-identical to the source
    assert all(p["outside_equal"] for p in rig.probes)


def test_solid_style_gives_flat_card_interior():
    from contracts import MaskStyle

    host = FakeLaneHost(card_detector(60, CARD), style=MaskStyle.SOLID)
    rig = Rig(60, host=host)
    rig.run()
    assert max(p["std"] for p in rig.probes) == 0.0
    assert all(p["outside_equal"] for p in rig.probes)


def test_no_frame_is_emitted_before_its_deadline_and_lag_is_small():
    rig = Rig(90)
    rig.run()
    for rec in rig.sink.records:
        deadline = T0 + rec.pts + D
        assert rec.written_at >= deadline - 1e-9, (rec.pts, rec.written_at - deadline)
    lags = list(rig.session.emit_lag)
    assert len(lags) == 90 and min(lags) >= 0.0 and max(lags) < 0.012
    assert [round(r.pts * FPS) for r in rig.sink.records] == list(range(90))
    assert all(len(r.audio) == 1600 for r in rig.sink.records)


def test_timing_jsonl_is_written(tmp_path):
    path = tmp_path / "timing" / "stages.jsonl"
    rig = Rig(60, config=_cfg(engine={"timing_log_path": str(path)}))
    rig.run()
    rig.session.stop()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    stages = {line["stage"] for line in lines}
    assert {"compose", "sink_write", "engine_delay", "emit_lag", "lane_fast"} <= stages
    assert sum(1 for line in lines if line["stage"] == "compose") == 60
    delays = [line["ms"] / 1000.0 for line in lines if line["stage"] == "engine_delay"]
    assert all(D - 1e-6 <= d < D + 0.1 for d in delays)  # arrival to write is the delay D, plus a few ms
    assert all(set(line) == {"stage", "seq", "pts", "t_start", "t_end", "ms"} for line in lines)
    assert all(line["ms"] >= 0 for line in lines)


def test_failsafe_detector_stops_only_protect_output():
    cfg = _cfg(engine={"protect_fill_s": 0.0})  # PROTECT shows the slate straight away
    host = FakeLaneHost(card_detector(120, CARD))
    rig = Rig(120, config=cfg, host=host)

    def killer(t: float) -> None:
        if t >= T0 + 45 / FPS and not host.killed:
            host.kill()  # the detector stops answering after frame ~45

    rig.run(hook=killer)
    probes = rig.probes
    covered = [i for i, p in enumerate(probes) if p["slate"] is None]
    assert covered and covered[-1] < 55, "frames well past the last result must not be LIVE"
    # last result at frame 45 (stride 3); FAST max age is 6 frames: frames 52.. are PROTECT
    for i in range(52, 120):
        assert probes[i]["slate"] == "PROTECT", (i, probes[i])
    assert rig.session.counts["PROTECT"] >= 68
    assert all(
        p["slate"] == "PROTECT" for p in probes[52:]
    )  # byte-identical slates: nothing of the card gets out


def test_failsafe_protect_fill_is_one_flat_colour_before_the_slate():
    host = FakeLaneHost(card_detector(120, CARD))
    rig = Rig(120, host=host)  # protect_fill_s defaults to 1.0

    def killer(t: float) -> None:
        if t >= T0 + 45 / FPS and not host.killed:
            host.kill()

    rig.run(hook=killer)
    flat = [i for i, p in enumerate(rig.probes) if p["flat"] and p["slate"] is None]
    assert flat and min(flat) >= 52
    assert any(p["slate"] == "PROTECT" for p in rig.probes[52:])  # then the slate takes over


def test_hold_via_bus_slates_the_next_frame_and_fences_until_new_frames():
    rig = Rig(150)
    hold_at = T0 + D + 0.21  # frames 0..6 are due by now
    release_at = hold_at + 0.1
    sent = {"hold": False, "release": False}

    def hook(t: float) -> None:
        if t >= hold_at and not sent["hold"]:
            sent["hold"] = True
            rig.bus.publish(Topic.HOLD, HoldCommand(HoldSource.HOTKEY, True, "test"))
        if t >= release_at and not sent["release"]:
            sent["release"] = True
            rig.bus.publish(Topic.HOLD, HoldCommand(HoldSource.HOTKEY, False, "test"))

    rig.run(hook=hook)
    probes = rig.probes
    states = ["slate" if p["slate"] else "live" for p in probes]
    first_hold = states.index("slate")
    assert 5 <= first_hold <= 8, first_hold
    # within one frame: every frame emitted after the command is a slate (incl. retro and fence)
    last_hold = len(states) - 1 - states[::-1].index("slate")
    assert all(s == "slate" for s in states[first_hold : last_hold + 1])
    # the fence: frames captured before the release (up to the capture high-water mark, frame 99)
    # stay slated after release
    assert last_hold == 99, last_hold
    assert all(s == "live" for s in states[last_hold + 1 :])
    assert rig.session.counts["HOLD"] == last_hold - first_hold + 1
    # audio is silent while held
    for rec in rig.sink.records[first_hold : last_hold + 1]:
        assert not rec.audio.any()


def test_retro_hold_covers_the_buffered_window_and_zeroes_its_audio():
    rig = Rig(150, beep_at=0.5)  # a 100 ms beep at 0.5 s, i.e. frames 15..17
    # hold at T0+3.72 with retro = 3.0: frames that arrived at or after T0+0.72 (frame 22 on) are
    # retro-held; the beep (arrival 0.5 s) is older than the window and passes
    hold_at = T0 + D + 0.72
    done = []

    def hook(t: float) -> None:
        if t >= hold_at and not done:
            done.append(t)
            rig.session.hold(HoldSource.HOTKEY, True, now=t)

    rig.run(hook=hook)
    probes = rig.probes
    # frames due before 3.72: 0..21 are emitted live (the beep at 15..17 is audible)
    assert all(p["slate"] is None for p in probes[:22])
    assert any(rec.audio.any() for rec in rig.sink.records[15:18])
    assert all(p["slate"] == "HOLD" for p in probes[22:])  # retro window, fence, then the standing hold


def test_av_sync_beep_and_stamp_align_within_20ms():
    rig = Rig(120, stamp_at=1.0, beep_at=1.0)
    rig.run()
    stamp_frame = next(i for i, p in enumerate(rig.probes) if p["stamp"])
    audio = np.concatenate([r.audio for r in rig.sink.records])
    first_sound = int(np.flatnonzero(audio)[0])
    stamp_sample = stamp_frame * 1600
    assert abs(first_sound - stamp_sample) / 48000 < 0.020, (first_sound, stamp_sample)


def test_zero_lanes_stays_protect():
    rig = Rig(60, host=FakeLaneHost(card_detector(60, CARD)))
    rig.host.active_lanes = frozenset()
    rig.session.engine.set_lanes(())
    rig.run()
    assert rig.session.counts["LIVE"] == 0
    assert all(p["slate"] == "PROTECT" or p["flat"] for p in rig.probes)


def test_status_and_stats_are_published():
    rig = Rig(90)
    sub = rig.bus.subscribe([Topic.ENGINE_STATUS, Topic.SAFETY_EVENT])
    rig.run()
    items = sub.drain()
    statuses = [e for t, e in items if t is Topic.ENGINE_STATUS]
    assert statuses and statuses[-1].contract_version == CONTRACT_VERSION
    assert any(s.state == "live" for s in statuses)
    live = [s for s in statuses if s.state == "live"][-1]
    assert live.fps is not None and 25 <= live.fps <= 35
    assert live.delay_s_p50 is not None and abs(live.delay_s_p50 - D) < 0.1
    # one masked event for the (single, untracked) card, never one per frame
    masked = [e for t, e in items if t is Topic.SAFETY_EVENT and e.action == "masked"]
    assert 1 <= len({e.event_id for e in masked}) <= 2 and len(masked) <= 4


def test_engine_starts_in_protect():
    rig = Rig(10)
    assert rig.session.engine.control.state == PROTECT
