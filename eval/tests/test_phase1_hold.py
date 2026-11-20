"""Hold test (T1.4): the real ZeroMQ wire format, the reference engine's hold semantics, and the analysis.

* ``HoldClient`` talks to an XSUB/XPUB proxy with the ``contracts`` wire format (D-2, D-35).
* ``RefEngine._on_hold`` / ``_state`` implement the engine's retro window and fence on synthetic frames.
* ``analyse_hold`` is run on synthetic taps of a correct engine and of engines that break one rule each.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from contracts import HoldCommand, HoldSource, Topic, dumps, loads
from eval.delay import TapFrame
from eval.holdbus import HOLD_TOPIC, HoldClient
from eval.phase1 import analyse_hold
from eval.refengine import RefEngine, _Chunk, _Frame

zmq = pytest.importorskip("zmq")

T0 = 5000.0
FPS = 30.0
D = 4.0
INGEST = 0.15
TAP_HOP = 0.05


# ---------------------------------------------------------------- wire format over a real broker


@pytest.fixture
def bus(tmp_path):
    xin = f"ipc://{tmp_path}/bus-in"
    xout = f"ipc://{tmp_path}/bus-out"
    ctx = zmq.Context()
    xsub = ctx.socket(zmq.XSUB)
    xsub.bind(xin)
    xpub = ctx.socket(zmq.XPUB)
    xpub.bind(xout)

    def run_proxy() -> None:
        try:
            zmq.proxy(xsub, xpub)
        except zmq.ZMQError:  # the fixture closed the sockets
            pass

    t = threading.Thread(target=run_proxy, daemon=True)
    t.start()
    yield ctx, xin, xout
    ctx.destroy(linger=0)


def test_hold_client_publishes_the_contract_message(bus):
    ctx, xin, xout = bus
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, HOLD_TOPIC.encode())
    sub.connect(xout)
    client = HoldClient(xin)
    try:
        assert client.connect(5.0), "no subscription reached the publisher side"
        t_send = client.send(True)
        assert sub.poll(3000), "the hold never arrived on the subscriber side"
        topic, payload = sub.recv_multipart()
        assert topic == b"control.hold" == Topic.HOLD.value.encode()
        cmd = loads(payload)
        assert isinstance(cmd, HoldCommand)
        assert cmd.active is True and cmd.source is HoldSource.API
        assert cmd.actor == "evalharness"
        assert payload == dumps(cmd)  # byte-identical to the contract's own encoding
        assert abs(time.monotonic() - t_send) < 3.0
        client.send(False, HoldSource.HOTKEY)
        assert sub.poll(3000)
        _, payload = sub.recv_multipart()
        cmd = loads(payload)
        assert cmd.active is False and cmd.source is HoldSource.HOTKEY
    finally:
        client.close()
        sub.close(0)


def test_hold_client_reports_no_listener(tmp_path):
    client = HoldClient(f"ipc://{tmp_path}/nobody-here")
    try:
        assert client.connect(0.4) is False
    finally:
        client.close()


def test_hold_client_needs_connect_before_send():
    with pytest.raises(RuntimeError):
        HoldClient().send(True)


# ---------------------------------------------------------------- reference engine hold semantics


def _engine(now: float, n: int = 10, spacing: float = 0.5) -> RefEngine:
    eng = RefEngine("in", "out", "card_hold:60", delay_s=4.0, retro_s=3.0)
    eng.origin = now - 20.0
    eng.t_first_emit = now - 10.0
    for i in range(n):  # frame i arrived (n - 1 - i) * spacing seconds ago, newest last
        arrival = now - (n - 1 - i) * spacing
        eng.vq.append(_Frame(i, i * spacing, arrival, i, np.zeros((4, 4, 3), np.uint8)))
        eng.aq.append(_Chunk(i * spacing, arrival, np.ones(16, np.float32)))
    eng.max_seq = n - 1
    return eng


def _cmd(active: bool) -> HoldCommand:
    return HoldCommand(source=HoldSource.API, active=active, actor="evalharness")


def test_hold_covers_the_retro_window_and_fences(monkeypatch):
    now = time.monotonic()
    eng = _engine(now)
    eng._on_hold(_cmd(True))
    retro = [f for f in eng.vq if f.held]
    # frames that arrived within the last retro_s = 3.0 s are held retroactively
    assert [f.seq for f in retro] == [4, 5, 6, 7, 8, 9]
    assert [c.held for c in eng.aq].count(True) == 6
    # every buffered frame, held or not, is HOLD (the fence covers the older ones)
    assert {eng._state(f, now) for f in eng.vq} == {"HOLD"}
    # release: old frames stay HOLD until a newer frame passes, which clears the fence
    eng._on_hold(_cmd(False))
    assert not any(eng.holds.values())
    assert all(eng._state(f, now) == "HOLD" for f in eng.vq)
    newer = _Frame(10, 5.0, now + 0.1, 10, np.zeros((4, 4, 3), np.uint8))
    eng.max_seq = 10
    assert eng._state(newer, now + 0.1) == "LIVE"
    assert eng.fence_seq is None


def test_hold_mutes_audio_and_slates_while_active():
    now = time.monotonic()
    eng = _engine(now, n=4, spacing=2.0)
    eng._on_hold(_cmd(True))
    f_new = _Frame(99, 9.0, now + 1.0, 99, np.zeros((4, 4, 3), np.uint8))
    eng.max_seq = 99
    assert eng._state(f_new, now + 1.0) == "HOLD"  # holds[api] is active, so even newer frames slate
    assert all(c.held for c in eng.aq if c.arrival >= now - 3.0)


def test_broken_engine_ignores_holds():
    now = time.monotonic()
    eng = _engine(now)
    eng.broken = True
    eng._on_hold(_cmd(True))
    assert not any(f.held for f in eng.vq) and not eng.holds
    assert eng._state(eng.vq[-1], now) == "LIVE"


def test_hold_retro_window_is_clamped_to_the_delay():
    assert RefEngine("i", "o", "idle:5", delay_s=3.0, retro_s=3.0).retro == 2.5


# ---------------------------------------------------------------- analyse_hold on synthetic taps


def _scenario(
    t_hold: float,
    t_release: float,
    leak_after: int = 0,
    no_retro: bool = False,
    no_fence: bool = False,
    audio_on: bool = False,
    seconds: float = 40.0,
    tmp_path=None,
):
    """A tap of an engine with delay D. ``HOLD iff emitted in [t_hold, t_release)`` plus the fence rule."""
    frames: list[TapFrame] = []
    audio: list[tuple[float, float]] = []
    lines = []
    n = int(seconds * FPS)
    held_extra = 0
    for i in range(n):
        capture = T0 + i / FPS
        arrival = capture + INGEST
        emitted = arrival + D
        t_read = emitted + TAP_HOP
        in_window = t_hold <= emitted < t_release
        fenced = emitted >= t_release and capture <= t_hold and not no_fence
        held = in_window or fenced
        if no_retro and capture >= t_hold - 3.0 and capture <= t_hold:
            held = False  # retro window not applied: those frames go out live
        if emitted >= t_hold and held_extra < leak_after and in_window:
            held = False  # the engine needed `leak_after` more frames to react
            held_extra += 1
        lines.append(
            {
                "stage": "engine_delay",
                "seq": i,
                "pts": i / FPS,
                "t_start": arrival,
                "t_end": emitted,
                "ms": D * 1000,
            }
        )
        if held:
            frames.append(TapFrame(None, t_read, "slate"))
        else:
            tf = TapFrame(i, t_read, "live", t_read - capture, i / FPS, "card_hold")
            tf.card_visible, tf.leaked, tf.similarity = True, False, 0.01
            frames.append(tf)
        audio.append((t_read, -20.0 if (audio_on or not held) else -90.0))
    path = tmp_path / "engine.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    res = SimpleNamespace(
        tap=SimpleNamespace(
            frames=frames,
            audio=audio,
            errors=[],
            reader=SimpleNamespace(
                video_decoded=len(frames),
                stamp_unreadable=0,
                slate_frames=sum(1 for f in frames if f.kind == "slate"),
                summary=lambda: {"av_offset_ms": {"max_abs_ms": 5.0, "n": 10}},
            ),
        ),
        engine_timing_path=path,
        engine_exit=None,
        cpu={},
        vram_mb={},
        spec=SimpleNamespace(delay_s=D),
    )
    return res


def _failed(checks):
    return [c.name for c in checks if not c.passed]


def test_analyse_hold_passes_a_correct_engine(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 8.0, tmp_path=tmp_path)
    checks, m = analyse_hold(res, t_hold, t_hold + 8.0, 3.0)
    assert _failed(checks) == [], [(c.name, c.value, c.note) for c in checks]
    assert m["hold"]["engine_alignment_valid"]
    assert m["hold"]["live_frames_emitted_after_hold"] == 0
    assert m["hold"]["live_frames_in_retro_window"] == 0
    assert m["hold"]["audio_max_dbfs_in_hold"] <= -50.0


def test_analyse_hold_passes_a_short_hold_that_needs_the_fence(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 2.0, tmp_path=tmp_path)
    checks, m = analyse_hold(res, t_hold, t_hold + 2.0, 3.0)
    assert _failed(checks) == []
    assert m["hold"]["older_than_hold_live_after_release"] == 0


def test_analyse_hold_catches_a_slow_slate(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 8.0, leak_after=12, tmp_path=tmp_path)
    checks, _ = analyse_hold(res, t_hold, t_hold + 8.0, 3.0)
    assert any("slate within 1 frame" in n for n in _failed(checks))


def test_analyse_hold_catches_a_missing_retro_window(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 8.0, no_retro=True, tmp_path=tmp_path)
    checks, _ = analyse_hold(res, t_hold, t_hold + 8.0, 3.0)
    failed = _failed(checks)
    assert any("last 3 s before the hold" in n for n in failed)


def test_analyse_hold_catches_a_broken_fence(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 2.0, no_fence=True, tmp_path=tmp_path)
    checks, _ = analyse_hold(res, t_hold, t_hold + 2.0, 3.0)
    assert any("fence" in n for n in _failed(checks))


def test_analyse_hold_catches_audio_left_on(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 8.0, audio_on=True, tmp_path=tmp_path)
    checks, _ = analyse_hold(res, t_hold, t_hold + 8.0, 3.0)
    assert any("audio level" in n for n in _failed(checks))


def test_analyse_hold_when_the_hold_was_never_sent(tmp_path):
    t_hold = T0 + 14.0
    res = _scenario(t_hold, t_hold + 8.0, tmp_path=tmp_path)
    checks, _ = analyse_hold(res, None, None, 3.0)
    assert _failed(checks) == ["hold and release were sent over the bus"]
