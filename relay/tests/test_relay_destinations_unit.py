"""Destinations: config to specs, failure isolation, backoff, the all-down hold, secrets. Fake muxers only."""

from __future__ import annotations

import logging
import threading
import time

import pytest
from pydantic import SecretStr

from contracts.config import Config
from contracts.enums import HoldSource, Topic
from contracts.fakes.bus import InProcessBus
from relay.destinations import (
    DestinationPublisher,
    DestinationSet,
    DestinationSpec,
    build_destinations,
    parse_peer,
)
from relay.encoder import KIND_AUDIO, KIND_VIDEO, EncodedPacket, EncodeResult, StreamInfo

KEY = "abcd-efgh-ijkl-mnop-qrst"
KEY2 = "live_123456789_AbCdEfGhIjKlMnOpQrSt"
INFO = StreamInfo(b"FLV", 1280, 720, 30, b"\x00\x00\x00\x01")


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def result(index: int, *, generation=1, key_every=10) -> EncodeResult:
    video = EncodedPacket(KIND_VIDEO, b"v", index, index, 1, index % key_every == 0)
    audio = EncodedPacket(KIND_AUDIO, b"a", index * 1600, index * 1600, 1600, False)
    return EncodeResult(generation, index, index / 30, (video, audio), 0.004)


# ------------------------------------------------------------------------------- config to specs
def cfg(**kw) -> Config:
    return Config(_env_file=None, **kw)


def test_defaults_enable_youtube_and_tap_and_skip_youtube_without_a_key():
    built = build_destinations(cfg(), tap_url="rtmp://127.0.0.1:1/shielded?user=a&pass=b", environ={})
    assert [s.kind for s in built.specs] == ["mediamtx_tap"]
    assert built.skipped == ("youtube:no_key",)  # a fixed code, never a value


def test_keys_come_from_secretstr_config_or_the_environment():
    from_config = build_destinations(cfg(youtube_stream_key=SecretStr(KEY)), tap_url=None, environ={})
    youtube = next(s for s in from_config.specs if s.kind == "youtube")
    assert (youtube.host, youtube.port, youtube.app, youtube.tls) == (
        "a.rtmps.youtube.com",
        443,
        "live2",
        True,
    )
    assert youtube.url(5555) == f"rtmp://127.0.0.1:5555/live2/{KEY}"
    from_env = build_destinations(cfg(), tap_url=None, environ={"YOUTUBE_STREAM_KEY": KEY2})
    assert from_env.specs[0].key.get_secret_value() == KEY2


def test_twitch_uses_its_own_host_and_app():
    config = cfg(
        twitch_stream_key=SecretStr(KEY2),
        destinations=[{"kind": "twitch", "url_host": "live.twitch.tv", "enabled": True}],
    )
    (twitch,) = build_destinations(config, tap_url=None, environ={}).specs
    assert (twitch.kind, twitch.host, twitch.app) == ("twitch", "live.twitch.tv", "app")
    assert twitch.url(1).startswith("rtmp://127.0.0.1:1/app/live_")


def test_there_is_no_two_destination_cap_and_names_are_unique():
    config = cfg(
        youtube_stream_key=SecretStr(KEY),
        twitch_stream_key=SecretStr(KEY2),
        destinations=[
            {"kind": "youtube"},
            {"kind": "youtube", "url_host": "b.rtmps.youtube.com"},
            {"kind": "twitch"},
            {"kind": "mediamtx_tap"},
        ],
    )
    built = build_destinations(config, tap_url="rtmp://127.0.0.1:1/shielded", environ={})
    assert [s.name for s in built.specs] == ["youtube", "youtube2", "twitch", "mediamtx_tap"]


def test_disabled_destinations_and_bad_keys_are_skipped_without_echoing_the_key():
    config = cfg(
        youtube_stream_key=SecretStr("bad key with spaces/../"),
        destinations=[{"kind": "youtube"}, {"kind": "twitch", "enabled": False}],
    )
    built = build_destinations(config, tap_url=None, environ={})
    assert built.specs == () and built.skipped == ("youtube:bad_key",)
    assert "bad key" not in repr(built)


def test_peer_parsing_rejects_anything_but_host_and_port():
    assert parse_peer("a.rtmps.youtube.com") == ("a.rtmps.youtube.com", 443)
    assert parse_peer("localhost:8322") == ("localhost", 8322)
    for bad in ("evil.com/path", "user@host", "host?x=1", "host:99999", "", "ho st", "host:0"):
        with pytest.raises(ValueError):
            parse_peer(bad)


def test_spec_repr_and_url_never_leak_a_key_unless_asked_for_the_connect_url():
    spec = DestinationSpec("youtube", "youtube", "a.rtmps.youtube.com", 443, "live2", SecretStr(KEY))
    assert KEY not in repr(spec) and KEY not in str(spec)
    assert f"{spec}" == repr(spec)


# ---------------------------------------------------------------------------------- fake muxers
class FakeMuxer:
    def __init__(self, owner):
        self.owner, self.packets, self.closed = owner, [], False

    def write(self, packet):
        if self.owner.write_error:
            raise ConnectionResetError(f"reset: {self.owner.last_url}")
        if self.owner.write_gate is not None:
            assert self.owner.write_gate.wait(10)
            if self.owner.write_error:
                raise ConnectionResetError("cut")
        self.packets.append(packet)

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, *, fail=False):
        self.fail, self.write_error, self.write_gate = fail, False, None
        self.muxers: list[FakeMuxer] = []
        self.urls: list[str] = []
        self.last_url = ""

    def __call__(self, url, info):
        self.last_url = url
        self.urls.append(url)
        if self.fail:
            raise ConnectionRefusedError(f"cannot connect to {url}")  # libav-style text that holds the URL
        muxer = FakeMuxer(self)
        self.muxers.append(muxer)
        return muxer

    @property
    def video_dts(self):
        return [p.dts for m in self.muxers for p in m.packets if p.kind == KIND_VIDEO]


def spec(name="youtube", key=KEY) -> DestinationSpec:
    return DestinationSpec(name, name, "a.rtmps.youtube.com", 443, "live2", SecretStr(key))


class FakeForwarder:
    port = 4242

    def __init__(self):
        self.resets = 0
        self.closed = False

    def reset_connections(self):
        self.resets += 1
        return 1

    def close(self):
        self.closed = True


def make(names, openers, *, bus=None, hold_after=0.1, **kw):
    forwarders = kw.get("forwarders", {})
    publishers = [
        DestinationPublisher(
            spec(name),
            fps=30,
            opener=openers[name],
            forwarder=forwarders.get(name) or FakeForwarder(),
            backoff=(0.05, 0.05),
            jitter=0.0,
        )  # fmt: skip
        for name in names
    ]
    return DestinationSet(
        publishers, bus=bus, session_id="s", hold_after_s=hold_after, stall_after_s=kw.get("stall", 6.0)
    ).start()


def feed(dset, start, count, *, sleep=0.004, **kw):
    for i in range(start, start + count):
        dset.offer(result(i, **kw), INFO, time.monotonic())
        time.sleep(sleep)


def test_a_destination_joins_on_a_keyframe_and_its_timestamps_start_at_zero():
    opener = Opener()
    dset = make(["youtube"], {"youtube": opener})
    try:
        feed(dset, 105, 30)  # frame 105 is not a keyframe; 110 is
        assert wait_until(lambda: opener.muxers and len(opener.muxers[0].packets) >= 20)
        first = opener.muxers[0].packets[0]
        assert first.kind == KIND_VIDEO and first.keyframe and first.dts == 0
        assert all(p.dts >= 0 for p in opener.muxers[0].packets)
        assert opener.urls[0] == f"rtmp://127.0.0.1:4242/live2/{KEY}"  # the URL exists only at connect time
    finally:
        dset.close()


def test_one_failing_destination_never_interrupts_the_others():
    good_a, good_b, bad = Opener(), Opener(), Opener(fail=True)
    bus = InProcessBus()
    dset = make(
        ["youtube", "twitch", "mediamtx_tap"],
        {"youtube": good_a, "twitch": bad, "mediamtx_tap": good_b},
        bus=bus,
    )
    try:
        feed(dset, 0, 60)
        assert wait_until(lambda: good_a.video_dts and len(good_a.video_dts) >= 50)
        assert wait_until(lambda: len(good_b.video_dts) >= 50)
        assert (
            good_a.video_dts == sorted(good_a.video_dts) == list(range(len(good_a.video_dts)))
        )  # none lost, in order
        twitch = next(p for p in dset.publishers if p.name == "twitch")
        assert twitch.state == "down" and twitch.failures >= 1 and twitch.last_error == "connect-failed"
        assert dset.hold_active is False and not bus.of(Topic.HOLD)  # two destinations are fine: no hold
        codes = [(e.source, e.detail_code) for e in bus.of(Topic.SAFETY_EVENT)]
        assert ("relay.egress.twitch", "destination_down") in codes
        assert ("relay.egress.youtube", "destination_up") in codes
    finally:
        dset.close()


def test_a_failed_destination_is_retried_with_backoff_and_recovers_on_its_own():
    flaky = Opener(fail=True)
    other = Opener()
    bus = InProcessBus()
    dset = make(["youtube", "twitch"], {"youtube": other, "twitch": flaky}, bus=bus)
    try:
        feed(dset, 0, 40)
        twitch = dset.publishers[1]
        assert wait_until(lambda: twitch.failures >= 2)  # retried, not abandoned
        flaky.fail = False
        feed(dset, 40, 60)
        assert wait_until(lambda: twitch.state == "up")
        assert [e.detail_code for e in bus.of(Topic.SAFETY_EVENT) if e.source.endswith("twitch")] == [
            "destination_down",
            "destination_up",
        ]
        assert len(other.video_dts) >= 90  # youtube never noticed
    finally:
        dset.close()


def test_auto_egress_hold_only_when_every_destination_is_down_and_released_on_the_first_up():
    a, b = Opener(fail=True), Opener(fail=True)
    bus = InProcessBus()
    dset = make(["youtube", "twitch"], {"youtube": a, "twitch": b}, bus=bus, hold_after=0.15)
    try:
        feed(dset, 0, 30)
        assert wait_until(lambda: dset.hold_active)
        holds = bus.of(Topic.HOLD)
        assert [(h.source, h.active) for h in holds] == [(HoldSource.AUTO_EGRESS, True)]
        held = [e for e in bus.of(Topic.SAFETY_EVENT) if e.detail_code == "egress_down"]
        assert len(held) == 1 and held[0].action == "held" and held[0].category.value == "hold"
        b.fail = False  # one destination comes back
        feed(dset, 30, 60)
        assert wait_until(lambda: not dset.hold_active)
        assert [(h.source, h.active) for h in bus.of(Topic.HOLD)] == [
            (HoldSource.AUTO_EGRESS, True),
            (HoldSource.AUTO_EGRESS, False),
        ]
    finally:
        dset.close()


def test_no_hold_while_destinations_are_merely_connecting_or_idle():
    slow = Opener()
    bus = InProcessBus()
    dset = make(["youtube"], {"youtube": slow}, bus=bus, hold_after=0.05)
    try:
        time.sleep(0.4)  # nothing offered yet: the destination is idle, not down
        assert dset.hold_active is False and not bus.of(Topic.HOLD)
    finally:
        dset.close()


def test_a_new_encoder_generation_reconnects_without_counting_as_a_failure():
    opener = Opener()
    dset = make(["youtube"], {"youtube": opener})
    try:
        feed(dset, 0, 20, generation=1)
        feed(dset, 0, 20, generation=2)
        assert wait_until(lambda: len(opener.muxers) == 2)
        assert opener.muxers[0].closed
        assert dset.publishers[0].failures == 0
        assert opener.muxers[1].packets[0].dts == 0 and opener.muxers[1].packets[0].keyframe
    finally:
        dset.close()


def test_queue_overflow_resets_a_destination_that_cannot_keep_up():
    opener = Opener()
    opener.write_gate = threading.Event()  # the first write blocks: a stalled destination
    dset = make(["youtube"], {"youtube": opener})
    publisher = dset.publishers[0]
    try:
        feed(dset, 0, 12, sleep=0.002)
        assert wait_until(lambda: opener.muxers)
        for i in range(12, 12 + publisher._limit // 2 + 40):  # twice the queue limit in packets
            dset.offer(result(i), INFO, time.monotonic())
        assert publisher.overflows >= 1 and publisher.stats()["queue"] <= publisher._limit
        opener.write_gate.set()
        assert wait_until(lambda: publisher.failures >= 1 or publisher.state == "up")
    finally:
        opener.write_gate.set()
        dset.close()


def test_the_watchdog_cuts_a_stalled_tls_connection_and_the_destination_recovers():
    opener = Opener()
    opener.write_gate = threading.Event()
    forwarder = FakeForwarder()
    dset = make(["youtube"], {"youtube": opener}, forwarders={"youtube": forwarder}, stall=0.2)

    def cut():  # what TLSForwarder.reset_connections does to a blocked write
        gate = opener.write_gate
        if gate is not None:
            opener.write_error = True
            gate.set()
        return 1

    forwarder.reset_connections = lambda: (setattr(forwarder, "resets", forwarder.resets + 1), cut())[1]
    try:
        feed(dset, 0, 12)
        assert wait_until(lambda: forwarder.resets >= 1, timeout=5)
        assert wait_until(lambda: dset.publishers[0].failures >= 1)
        opener.write_error, opener.write_gate = False, None
        feed(dset, 12, 60)
        assert wait_until(lambda: dset.publishers[0].state == "up")
    finally:
        dset.close()
    assert forwarder.closed  # closing the set closes each destination's forwarder


def test_egress_write_timing_comes_from_the_primary_destination_only():
    a, b = Opener(), Opener()
    bus = InProcessBus()
    dset = make(["youtube", "twitch"], {"youtube": a, "twitch": b}, bus=bus)
    try:
        feed(dset, 0, 60)
        assert wait_until(lambda: len(a.video_dts) >= 55 and len(b.video_dts) >= 55)
        timings = [t for t in bus.of(Topic.TIMING) if t.stage == "egress_write"]
        assert len(timings) >= 50 and len({t.seq for t in timings}) == len(
            timings
        )  # one per frame, not per destination
        assert all(t.t_end >= t.t_start for t in timings)
    finally:
        dset.close()


def test_no_stream_key_in_logs_events_stats_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    bad = Opener(fail=True)
    bus = InProcessBus()
    dset = make(["youtube"], {"youtube": bad}, bus=bus, hold_after=0.05)
    try:
        feed(dset, 0, 40)
        assert wait_until(lambda: dset.hold_active)
        text = "\n".join(
            [
                caplog.text,
                repr(dset.stats()),
                repr(bus.history),
                repr(dset.publishers),
                repr(dset.publishers[0].spec),
            ]
        )
        assert KEY not in text, "the stream key leaked"
        assert "cannot connect" not in caplog.text  # libav-style text (which holds the URL) is never logged
        assert "connect failed: ConnectionRefusedError" in caplog.text  # only the class name is
    finally:
        dset.close()
