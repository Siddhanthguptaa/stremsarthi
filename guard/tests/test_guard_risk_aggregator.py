"""T5.2 risk aggregator, meter, sources and bus service. Virtual clock, CPU only, no network."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time

import pytest

from contracts.config import GuardConfig
from contracts.detection import Detection
from contracts.enums import Category, Modality, Topic
from contracts.events import RiskMeter, RiskSignal, SafetyEvent
from contracts.fakes import InProcessBus
from contracts.geometry import Quad
from contracts.speech import Transcript, Word
from guard.risk import LevelTracker, RiskAggregator, RiskService, StreamClock, risky_text_signal
from guard.risk.service import stream_wall_ts

Q = Quad.from_xyxy(0, 0, 10, 10)


def sig(source: str, score: float, ttl: float, label: str, pts: float | None, cat: Category) -> RiskSignal:
    return RiskSignal("w", pts, source, cat, score, ttl, label)  # type: ignore[arg-type]


def music(score: float = 0.8, pts: float | None = 0.0) -> RiskSignal:
    return sig("music", score, 10.0, "music", pts, Category.MUSIC)


def fp(pts: float | None = 0.0) -> RiskSignal:
    return sig("fingerprint", 0.95, 60.0, "fp:demo_03", pts, Category.COPYRIGHT_MATCH)


def l2(pts: float | None = 0.0) -> RiskSignal:
    return sig("profanity", 0.6, 30.0, "profanity:L2", pts, Category.PROFANITY)


def slur(pts: float | None = 0.0) -> RiskSignal:
    return sig("slur", 0.9, 120.0, "slur:L3", pts, Category.PROFANITY)


def agg(**kw) -> RiskAggregator:
    return RiskAggregator(GuardConfig(**kw), wall_ts_fn=stream_wall_ts)


# ----------------------------------------------------------------------------- maths (spec cases)
def test_music_only_is_yellow_at_028() -> None:
    a = agg()
    u = a.add(music(0.8), 0.0)
    assert u.meter.score == pytest.approx(0.28) and u.meter.level == "yellow"
    assert u.meter.top_sources == ("music",)


def test_fingerprint_is_red_at_095() -> None:
    u = agg().add(fp(), 0.0)
    assert u.meter.score == pytest.approx(0.95) and u.meter.level == "red"


def test_one_l2_hit_is_030_and_two_hits_score_higher() -> None:
    a, b = agg(), agg()
    one = a.add(l2(0.0), 0.0).meter.score
    assert one == pytest.approx(0.30)
    b.add(l2(0.0), 0.0)
    two = b.add(l2(2.0), 2.0).meter.score
    assert two > a.score(2.0)


def test_signal_dropped_after_ttl_and_score_bounded() -> None:
    a = agg()
    a.add(l2(0.0), 0.0)
    assert a.score(29.9) > 0
    assert a.add_many([], 30.1).meter.score == 0.0
    b = agg()
    for i in range(300):
        b.add(slur(float(i) * 0.01), i * 0.01)
        b.add(fp(i * 0.01), i * 0.01)
    assert 0.0 <= b.score(3.0) <= 1.0


def test_repeating_music_refreshes_instead_of_stacking() -> None:
    a = agg()
    last = None
    for k in range(30):
        last = a.add(music(0.8, float(k)), float(k))
    assert last is not None and last.meter.score == pytest.approx(0.28, abs=1e-6)
    assert last.meter.level == "yellow"


def test_repeated_risky_text_does_not_stack() -> None:
    a = agg()
    for k in range(100):
        u = a.add(sig("risky_text", 0.4, 10.0, "risky_text", k * 0.1, Category.RISKY_TEXT), k * 0.1)
    assert u.meter.score == pytest.approx(0.6 * 0.4)  # one slot, 0.24: still green
    assert u.meter.level == "green"


def test_decay_halves_every_third_of_ttl() -> None:
    s = fp()
    assert RiskAggregator.contribution(s, 0.0, 20.0) == pytest.approx(0.95 * 0.5)
    assert RiskAggregator.contribution(s, 0.0, 60.1) == 0.0


def test_levels_cover_orange_and_red_bands() -> None:
    t = LevelTracker()
    assert [t.update(s) for s in (0.1, 0.3, 0.6, 0.8)] == ["green", "yellow", "orange", "red"]


# ----------------------------------------------------------------------------- hysteresis and cooldown (case 8)
def test_hysteresis_green_only_below_020() -> None:
    a = agg()
    a.add(l2(0.0), 0.0)
    levels = {}
    for t in (2.0, 2.7, 4.0, 5.5, 6.0):
        u = a.tick(t) if t != 2.0 else a.add_many([], t)
        assert u is not None
        levels[t] = (u.meter.level, u.meter.score)
    assert levels[2.7][1] < 0.25 and levels[2.7][0] == "yellow"  # below 0.25 but not below 0.20
    assert levels[5.5][0] == "yellow" and levels[5.5][1] > 0.20
    assert levels[6.0][0] == "green" and levels[6.0][1] < 0.20


def test_no_flicker_around_a_band_edge() -> None:
    t = LevelTracker()
    seq = [0.26, 0.24, 0.26, 0.22, 0.27, 0.21, 0.19, 0.26]
    out = [t.update(s) for s in seq]
    assert out == ["yellow"] * 6 + ["green", "yellow"]


def test_same_source_cannot_warn_twice_within_20s() -> None:
    a = agg()
    warns = []
    for t in (0.0, 1.0, 2.0):  # yellow, yellow, orange: two level increases, same top source
        warns += a.add(l2(t), t).warnings
    assert [w.detail_code for w in warns] == ["profanity"]
    # after decay to green and the cooldown, the same source may warn again
    for t in range(3, 40):
        a.tick(float(t))
    assert a.level == "green"
    warns2 = a.add(l2(40.0), 40.0).warnings
    assert [w.detail_code for w in warns2] == ["profanity"]


def test_other_source_warns_during_cooldown_and_event_shape() -> None:
    a = agg()
    a.add(l2(0.0), 0.0)
    (w,) = a.add(fp(1.0), 1.0).warnings
    assert isinstance(w, SafetyEvent)
    assert (w.action, w.modality, w.category, w.detail_code) == (
        "warned",
        Modality.GUARD,
        Category.COPYRIGHT_MATCH,
        "fingerprint",
    )
    assert w.bbox is None and 0.0 <= w.confidence <= 1.0


def test_level_decrease_never_warns() -> None:
    a = agg()
    a.add(fp(0.0), 0.0)
    for t in range(1, 120):
        u = a.tick(float(t))
        assert u is None or u.warnings == ()


# ----------------------------------------------------------------------------- publishing (case 9, response time)
def test_meter_ticks_at_2hz_within_10_percent() -> None:
    a = agg()
    times = []
    t = 0.0
    while t < 20.0:
        if a.tick(round(t, 3)) is not None:
            times.append(t)
        t += 0.01
    gaps = [b - c for c, b in zip(times, times[1:], strict=False)]
    assert len(times) >= 39
    assert all(0.45 <= g <= 0.55 for g in gaps), (min(gaps), max(gaps))


def test_new_signal_publishes_immediately_between_ticks() -> None:
    a = agg()
    assert a.tick(0.0) is not None and a.tick(0.1) is None
    u = a.add(music(), 0.1)
    assert u.meter.level == "yellow"


def test_meter_response_time_for_music_onset() -> None:
    a = agg()
    a.tick(0.0)
    t0 = time.perf_counter()
    u = a.add(music(0.8, 10.0), 10.0)
    cpu_ms = (time.perf_counter() - t0) * 1000
    print(f"meter response: 0.0 s virtual after the music signal, {cpu_ms:.3f} ms CPU")
    assert u.meter.level == "yellow" and cpu_ms < 5.0


def test_aggregator_is_deterministic() -> None:
    def run() -> list[tuple]:
        a = agg()
        out = []
        for k in range(40):
            now = k * 0.5
            ups = [a.add(s, now) for s in ([l2(now)] if k % 7 == 0 else [])]
            ups.append(a.tick(now))
            out += [(u.meter.level, u.meter.score, u.meter.wall_ts) for u in ups if u is not None]
        return out

    assert run() == run()


# ----------------------------------------------------------------------------- sources (case 6)
def det(cat: Category, conf: float, pts: float = 3.0) -> Detection:
    return Detection("text", cat, Q, conf, 1, pts, 0.0)


def test_risky_text_detection_becomes_signal_040_ttl_10() -> None:
    s = risky_text_signal(det(Category.RISKY_TEXT, 0.8))
    assert s is not None
    assert (s.source, s.score, s.ttl_s, s.label, s.pts) == (
        "risky_text",
        pytest.approx(0.4),
        10.0,
        "risky_text",
        3.0,
    )
    assert risky_text_signal(det(Category.PHONE_NUMBER, 0.9)) is None


# ----------------------------------------------------------------------------- service on the fake bus
def tr(words: list[tuple[str, float, float]]) -> Transcript:
    ws = tuple(Word(t, a, b) for t, a, b in words)
    return Transcript(ws[0].t0, ws[-1].t1, " ".join(t for t, _, _ in words), ws, "hi", "local", 0.3)


def test_service_transcript_to_signal_meter_and_warning() -> None:
    bus = InProcessBus()
    svc = RiskService(GuardConfig(), bus)
    out = svc.handle(Topic.TRANSCRIPT, tr([("tu", 0.0, 0.2), ("chutiya", 0.3, 0.8)]), now=1.0)
    topics = [t for t, _ in out]
    assert Topic.RISK in topics and Topic.RISK_METER in topics and Topic.SAFETY_EVENT in topics
    assert Topic.TIMING in topics
    assert bus.of(Topic.RISK)[0].label == "profanity:L2"
    assert bus.of(Topic.RISK_METER)[-1].level == "yellow"


def test_service_ignores_its_own_risk_signal_and_passes_foreign_ones() -> None:
    bus = InProcessBus()
    svc = RiskService(GuardConfig(), bus)
    svc.handle(Topic.TRANSCRIPT, tr([("chutiya", 0.0, 0.5)]), now=1.0)
    own = bus.of(Topic.RISK)[0]
    assert svc.handle(Topic.RISK, own, now=1.1) == []  # no double counting
    out = svc.handle(Topic.RISK, fp(1.0), now=1.2)
    assert [t for t, _ in out][0] is Topic.RISK_METER  # not re-published on guard.risk
    assert bus.of(Topic.RISK_METER)[-1].level == "red"


def test_service_risky_text_and_config_off_switches() -> None:
    svc = RiskService(GuardConfig(), None)
    out = svc.handle(Topic.DETECTION, det(Category.RISKY_TEXT, 1.0), now=3.0)
    assert any(t is Topic.RISK for t, _ in out)
    off = RiskService(GuardConfig(enabled=False), None)
    assert off.handle(Topic.DETECTION, det(Category.RISKY_TEXT, 1.0), now=3.0) == []
    noprof = RiskService(GuardConfig(profanity_enabled=False), None)
    assert noprof.handle(Topic.TRANSCRIPT, tr([("chutiya", 0.0, 0.5)]), now=1.0) == []


def test_service_tick_follows_stream_clock() -> None:
    mono = [100.0]
    clock = StreamClock(lambda: mono[0])
    svc = RiskService(GuardConfig(), None, clock=clock)
    svc.handle(Topic.DETECTION, det(Category.RISKY_TEXT, 1.0, pts=50.0))
    assert clock.now() == pytest.approx(50.0)
    mono[0] += 7.0
    assert clock.now() == pytest.approx(57.0)
    assert svc.tick() != []


@pytest.mark.asyncio
async def test_service_run_loop_publishes_meter_on_bus() -> None:
    bus = InProcessBus()
    svc = RiskService(GuardConfig(meter_hz=10.0), bus)
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await asyncio.sleep(0.05)
    bus.publish(Topic.TRANSCRIPT, tr([("bhosdike", 0.0, 0.5)]))
    await asyncio.sleep(0.4)
    stop.set()
    bus.publish(Topic.RISK, fp(0.0))  # wake the loop so it can exit
    await asyncio.wait_for(task, 2.0)
    meters = bus.of(Topic.RISK_METER)
    assert any(isinstance(m, RiskMeter) and m.level != "green" for m in meters)
    assert len(meters) >= 3  # immediate publish plus 10 Hz ticks


# ----------------------------------------------------------------------------- privacy (case 10)
def test_no_text_in_outputs_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    svc = RiskService(GuardConfig(), None)
    out = svc.handle(Topic.TRANSCRIPT, tr([("madarchod", 0.0, 0.5), ("bhenchod", 3.0, 3.5)]), now=4.0)
    dumped = " ".join(repr(e) + str(dataclasses.asdict(e)) for _, e in out if dataclasses.is_dataclass(e))
    assert "madarchod" not in dumped and "bhenchod" not in dumped
    assert all("chod" not in r.getMessage() for r in caplog.records)


# ----------------------------------------------------------------------------- failure containment
def test_bad_event_is_contained_logged_once_and_later_signals_still_work(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    svc = RiskService(GuardConfig(), None)
    real = svc.sources.from_event
    calls = {"n": 0}

    def flaky(topic, event):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise ValueError("secret-transcript-text")
        return real(topic, event)

    svc.sources.from_event = flaky  # type: ignore[method-assign]
    for _ in range(3):
        assert svc.handle(Topic.TRANSCRIPT, tr([("chutiya", 0.0, 0.5)]), now=1.0) == []
    assert svc.error_counts == {"ValueError": 3}
    msgs = [r.getMessage() for r in caplog.records if r.name == "guard.risk"]
    assert len(msgs) == 1 and "ValueError" in msgs[0] and "secret-transcript-text" not in msgs[0]
    assert svc._own == []  # nothing half-registered
    out = svc.handle(Topic.TRANSCRIPT, tr([("bhosdike", 5.0, 5.5)]), now=5.5)
    assert any(t is Topic.RISK_METER for t, _ in out)


def test_failing_tick_does_not_stop_later_meter_updates() -> None:
    svc = RiskService(GuardConfig(), None)
    real = svc.agg.tick
    state = {"fail": True}

    def flaky(now):
        if state["fail"]:
            raise RuntimeError("boom")
        return real(now)

    svc.agg.tick = flaky  # type: ignore[method-assign]
    assert svc.tick(0.0) == [] and svc.tick(0.5) == []
    assert svc.error_counts == {"RuntimeError": 2}
    state["fail"] = False
    assert [t for t, _ in svc.tick(1.0)] == [Topic.RISK_METER]


@pytest.mark.asyncio
async def test_run_loop_survives_bad_event_and_failing_tick() -> None:
    bus = InProcessBus()
    svc = RiskService(GuardConfig(meter_hz=10.0), bus)
    real_from, real_tick = svc.sources.from_event, svc.agg.tick
    seen = {"ev": 0, "tk": 0}

    def bad_from(topic, event):
        seen["ev"] += 1
        if seen["ev"] == 1:
            raise ValueError("x")
        return real_from(topic, event)

    def bad_tick(now):
        seen["tk"] += 1
        if seen["tk"] <= 2:
            raise RuntimeError("y")
        return real_tick(now)

    svc.sources.from_event = bad_from  # type: ignore[method-assign]
    svc.agg.tick = bad_tick  # type: ignore[method-assign]
    stop = asyncio.Event()
    task = asyncio.create_task(svc.run(stop))
    await asyncio.sleep(0.05)
    bus.publish(Topic.TRANSCRIPT, tr([("chutiya", 0.0, 0.5)]))  # fails
    bus.publish(Topic.TRANSCRIPT, tr([("bhosdike", 3.0, 3.5)]))  # must still be processed
    await asyncio.sleep(0.4)
    stop.set()
    bus.publish(Topic.RISK, fp(0.0))
    await asyncio.wait_for(task, 2.0)
    assert svc.error_counts.get("ValueError") == 1 and svc.error_counts.get("RuntimeError") == 2
    assert any(m.level != "green" for m in bus.of(Topic.RISK_METER))


def test_service_stamps_real_wall_time_and_takes_the_session_id_from_the_environment(monkeypatch):
    from datetime import datetime

    from guard.risk import service as svc_mod

    monkeypatch.setenv("STREAMSARTHI_SESSION_ID", "sess-abc123")
    monkeypatch.setattr(svc_mod.time, "time", lambda: 1_800_000_000.0)  # 2027-01-15, far from the old epoch
    svc = RiskService(GuardConfig(), None)
    ts = svc.agg._wall(12.5)  # stream time must not leak into the wall stamp
    assert datetime.fromisoformat(ts).timestamp() == pytest.approx(1_800_000_000.0)
    assert svc.agg._session == "sess-abc123"
    monkeypatch.delenv("STREAMSARTHI_SESSION_ID")
    assert RiskService(GuardConfig(), None).agg._session == "session"  # fallback: the previous default
    for bad in ("x" * 65, "has space", "semi;colon", "../etc", "नम"):
        monkeypatch.setenv("STREAMSARTHI_SESSION_ID", bad)
        assert RiskService(GuardConfig(), None).agg._session == "session"  # invalid values are ignored
    monkeypatch.setenv("STREAMSARTHI_SESSION_ID", "A_b-9" + "z" * 59)  # exactly 64 characters is fine
    assert RiskService(GuardConfig(), None).agg._session == "A_b-9" + "z" * 59
    assert RiskService(GuardConfig(), None, session_id="explicit").agg._session == "explicit"
