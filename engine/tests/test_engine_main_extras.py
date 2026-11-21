"""``runtime.extras``: optional services built after audio and before relay, started and closed by the
assembly. Fake builders only (virtual: no network, no real guard or captions)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest

from contracts import Config
from engine import main as engine_main
from engine.main import Extra, ModuleLoadError, assemble, run

FAKES = {
    "relay": "engine.session_fakes:build_relay",
    "vision": "engine.session_fakes:build_vision",
    "audio": "engine.session_fakes:build_audio",
}
SECRET = "SECRET-TEXT-9931"


def make_config(extras: dict[str, str]) -> Config:
    return Config(_env_file=None, delay_seconds=3.0, runtime={"modules": FAKES, "extras": extras})


class SyncService:
    def __init__(self, log: list[str], name: str) -> None:
        self.log, self.name, self.started, self.closed = log, name, False, False

    def start(self) -> None:
        self.started = True
        self.log.append(f"start:{self.name}")

    def close(self) -> None:
        self.closed = True
        self.log.append(f"close:{self.name}")


class AsyncService:
    """Only ``async def run(stop)``, like guard.risk.service.RiskService."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.thread_name = ""
        self.loop_closed_after = False

    async def run(self, stop: asyncio.Event | None = None) -> None:
        self.thread_name = threading.current_thread().name
        self.entered.set()
        try:
            await asyncio.sleep(3600)  # a subscriber loop that only ends by cancellation
        finally:
            self.finished.set()


class StartRequiresArgs:
    """Like guard.music.MusicGuard: start(callback) cannot be called by the assembly."""

    def __init__(self) -> None:
        self.stopped = False

    def start(self, callback, interval_s: float = 0.25) -> None:  # pragma: no cover - must not be called
        raise AssertionError("assembly must not call start() with no arguments")

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def builders(monkeypatch):
    """Map fake extra paths to builders; every other path resolves normally."""
    table: dict = {}
    real = engine_main.load_builder
    monkeypatch.setattr(
        engine_main, "load_builder", lambda path: table[path] if path in table else real(path)
    )
    return table


def assemble_fake(extras, **kw):
    return assemble(make_config(extras), broker=False, **kw)


def test_extras_are_built_after_audio_and_before_relay_and_closed_in_reverse(builders):
    order: list[str] = []
    log: list[str] = []
    for name in ("audio", "relay"):
        inner = engine_main.load_builder(FAKES[name])
        builders[f"fake.{name}:build"] = lambda c, b, inner=inner, name=name: (
            order.append(name),
            inner(c, b),
        )[1]
    builders["fake.a:build"] = lambda c, b: (order.append("a"), SyncService(log, "a"))[1]
    builders["fake.b:build"] = lambda c, b: (order.append("b"), SyncService(log, "b"))[1]
    cfg = Config(
        _env_file=None,
        delay_seconds=3.0,
        runtime={
            "modules": {**FAKES, "audio": "fake.audio:build", "relay": "fake.relay:build"},
            "extras": {"a": "fake.a:build", "b": "fake.b:build"},
        },
    )
    asm = assemble(cfg, broker=False)
    assert order == ["audio", "a", "b", "relay"]
    assert [e.name for e in asm.extras] == ["a", "b"]
    asm.start()
    assert log[:2] == ["start:a", "start:b"]
    asm.shutdown()
    assert log[2:] == ["close:b", "close:a"]


def test_a_failing_extra_is_a_warning_with_only_the_class_name(builders, caplog):
    def boom(config, bus):
        raise RuntimeError(SECRET)

    log: list[str] = []
    builders["fake.boom:build"] = boom
    builders["fake.ok:build"] = lambda c, b: SyncService(log, "ok")
    with caplog.at_level(logging.DEBUG):
        asm = assemble_fake(
            {"boom": "fake.boom:build", "missing": "no.such.module:build", "ok": "fake.ok:build"}
        )
    assert [e.name for e in asm.extras] == ["ok"]  # the others were dropped, the engine still assembled
    assert "RuntimeError" in caplog.text and "ModuleLoadError" in caplog.text
    assert SECRET not in caplog.text
    asm.shutdown()


def test_start_failure_is_not_fatal_and_the_service_is_closed(builders, caplog):
    log: list[str] = []

    class BadStart(SyncService):
        def start(self) -> None:
            raise ValueError(SECRET)

    builders["fake.bad:build"] = lambda c, b: BadStart(log, "bad")
    builders["fake.good:build"] = lambda c, b: SyncService(log, "good")
    asm = assemble_fake({"bad": "fake.bad:build", "good": "fake.good:build"})
    with caplog.at_level(logging.DEBUG):
        asm.start()
    assert "close:bad" in log and "start:good" in log
    assert SECRET not in caplog.text and "ValueError" in caplog.text
    assert asm.session._threads  # the session still started
    asm.shutdown()
    assert "close:good" in log


def test_relay_failure_closes_already_built_extras(builders):
    log: list[str] = []
    builders["fake.a:build"] = lambda c, b: SyncService(log, "a")
    builders["fake.relay:build"] = lambda c, b: (_ for _ in ()).throw(RuntimeError("no listener"))
    cfg = Config(
        _env_file=None,
        delay_seconds=3.0,
        runtime={"modules": {**FAKES, "relay": "fake.relay:build"}, "extras": {"a": "fake.a:build"}},
    )
    with pytest.raises(ModuleLoadError):
        assemble(cfg, broker=False)
    assert log == ["close:a"]


def test_skip_accepts_extra_names_and_rejects_unknown(builders):
    built: list[str] = []
    builders["fake.a:build"] = lambda c, b: (built.append("a"), SyncService([], "a"))[1]
    asm = assemble_fake({"a": "fake.a:build"}, skip=["a"])
    assert built == [] and asm.extras == []
    asm.shutdown()
    with pytest.raises(ModuleLoadError):
        assemble_fake({"a": "fake.a:build"}, skip=["nonsense"])


def test_async_run_only_service_runs_on_its_own_thread_and_is_stopped_on_close():
    svc = AsyncService()
    extra = Extra("guard_risk", svc)
    assert extra.start() is True
    assert svc.entered.wait(3.0)
    assert svc.thread_name == "engine-extra-guard_risk"
    runner_thread = extra._runner._thread
    assert runner_thread.is_alive() and runner_thread.daemon
    extra.close()
    assert svc.finished.wait(3.0)  # cancelled
    assert not runner_thread.is_alive()
    extra.close()  # idempotent


def test_async_run_that_raises_is_logged_once_and_not_retried(caplog):
    class Crashes:
        calls = 0

        async def run(self, stop=None):
            Crashes.calls += 1
            raise RuntimeError(SECRET)

    extra = Extra("x", Crashes())
    with caplog.at_level(logging.DEBUG):
        assert extra.start() is True
        extra._runner._thread.join(3.0)
        time.sleep(0.1)
    assert Crashes.calls == 1 and SECRET not in caplog.text and "RuntimeError" in caplog.text
    extra.close()


def test_run_without_stop_parameter_is_supported():
    class NoStop:
        def __init__(self):
            self.entered = threading.Event()

        async def run(self):
            self.entered.set()
            await asyncio.sleep(3600)

    svc = NoStop()
    extra = Extra("n", svc)
    assert extra.start() and svc.entered.wait(3.0)
    extra.close()
    assert not extra._runner._thread


def test_start_that_needs_arguments_is_not_called_but_stop_is_used_to_close():
    svc = StartRequiresArgs()
    extra = Extra("music", svc)
    assert extra.start() is False  # no usable lifecycle: warning, nothing called
    extra.close()
    assert svc.stopped


def test_assembly_runs_and_shuts_down_with_an_async_extra(builders):
    svc = AsyncService()
    builders["fake.risk:build"] = lambda c, b: svc
    asm = assemble_fake({"risk": "fake.risk:build"})
    stop = threading.Event()
    worker = threading.Thread(target=run, args=(asm, stop), kwargs={"poll_s": 0.05})
    worker.start()
    assert svc.entered.wait(5.0)
    stop.set()
    worker.join(10.0)
    assert not worker.is_alive() and svc.finished.is_set()


def test_default_extra_guard_risk_loads_and_stops_cleanly():
    """The real default (guard.risk.service:build), read-only use: it starts on a thread and closes."""
    asm = assemble(Config(_env_file=None, delay_seconds=3.0, runtime={"modules": FAKES}), broker=False)
    names = [e.name for e in asm.extras]
    if "guard_risk" not in names:
        pytest.skip("guard.risk.service not importable here")
    asm.start()
    time.sleep(0.2)
    asm.shutdown()
    assert not any(t.is_alive() for t in threading.enumerate() if t.name.startswith("engine-extra-"))


def test_object_with_start_close_and_async_run_uses_start_and_close():
    """audio.captions.CaptionService: it hosts its own thread, so the assembly must not start run() a second time."""

    class Hosted(SyncService):
        async def run(self) -> None:  # pragma: no cover - must not be scheduled by the assembly
            raise AssertionError("run() must not be started by the assembly")

    log: list[str] = []
    extra = Extra("captions", Hosted(log, "c"))
    assert extra._runner is None and extra.start() is True
    extra.close()
    assert log == ["start:c", "close:c"]


# ------------------------------------------------------------------ audio tap to extras that expose feed()

import numpy as np  # noqa: E402

from engine.session import Session  # noqa: E402
from engine.session_fakes import FakeAudioHost, RecordingSink, SyntheticSource  # noqa: E402


class FeedService(SyncService):
    def __init__(self, log: list[str], name: str = "music") -> None:
        super().__init__(log, name)
        self.chunks: list = []
        self.lock = threading.Lock()

    def feed(self, chunk) -> None:
        with self.lock:
            self.chunks.append(chunk)


def tap_session(*, audio=True):
    cfg = Config(_env_file=None, delay_seconds=3.0)
    host = FakeAudioHost() if audio else None
    session = Session(cfg, SyntheticSource(1), RecordingSink(), audio_host=host)
    return session, host


def push(session, n: int, first: int = 0) -> None:
    for k in range(n):  # 20 ms blocks at 48 kHz
        index = (first + k) * 960
        session.on_audio(index, (np.arange(960) % 200 - 100).astype(np.int16))


def wait_for(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_every_chunk_reaches_the_tap_in_order_at_16k_and_the_host_still_gets_them():
    session, host = tap_session()
    svc = FeedService([])
    session.add_audio_tap("music", svc.feed)
    push(session, 50)
    assert wait_for(lambda: len(svc.chunks) == 50)
    assert [c.seq for c in svc.chunks] == list(range(50))
    assert all(c.sample_rate == 16000 and c.pcm.dtype == np.int16 and len(c.pcm) == 320 for c in svc.chunks)
    assert len(host.chunks) == 50 and svc.chunks[7] is host.chunks[7]  # shared, not copied
    session.stop()


def test_tap_works_without_an_audio_host():
    session, _ = tap_session(audio=False)
    svc = FeedService([])
    session.add_audio_tap("music", svc.feed)
    push(session, 10)
    assert wait_for(lambda: len(svc.chunks) == 10)
    session.stop()


def test_a_raising_feed_is_contained_logged_once_by_class_and_the_audio_path_is_unaffected(caplog):
    session, host = tap_session()
    calls = []

    def bad(chunk):
        calls.append(1)
        raise ValueError(SECRET)

    tap = session.add_audio_tap("music", bad)
    with caplog.at_level(logging.DEBUG):
        push(session, 40)
        assert wait_for(lambda: len(calls) == 40)
    assert tap.errors == 40 and len(host.chunks) == 40
    assert session.counts["audio_errors"] == 0
    assert sum("ValueError" in r.message for r in caplog.records) == 1  # once
    assert SECRET not in caplog.text
    session.stop()


def test_a_blocked_feed_never_slows_the_audio_path_and_overflow_is_dropped():
    session, host = tap_session()
    release = threading.Event()
    seen = []

    def stuck(chunk):
        seen.append(1)
        release.wait(10.0)

    tap = session.add_audio_tap("music", stuck)
    began = time.perf_counter()
    push(session, 600)  # more than the 256-chunk queue
    elapsed = time.perf_counter() - began
    assert len(host.chunks) == 600
    assert elapsed < 2.0  # 600 blocks of audio ingest while the tap is stuck: no waiting on it
    assert tap.dropped >= 600 - 256 - 2
    release.set()
    session.stop()  # does not hang on the stuck worker
    assert wait_for(lambda: not tap._thread.is_alive())


def test_nothing_is_fed_while_the_audio_is_unchecked():
    session, host = tap_session()
    svc = FeedService([])
    session.add_audio_tap("music", svc.feed)
    push(session, 5)
    assert wait_for(lambda: len(svc.chunks) == 5)
    session._audio_stale = True  # the watchdog says the audio module is silent
    push(session, 5, first=5)
    time.sleep(0.1)
    assert len(svc.chunks) == 5 and len(host.chunks) == 10
    session._audio_stale = False
    push(session, 5, first=10)
    assert wait_for(lambda: len(svc.chunks) == 10)
    session.stop()


def test_nothing_is_fed_after_stop():
    session, _ = tap_session()
    svc = FeedService([])
    tap = session.add_audio_tap("music", svc.feed)
    push(session, 5)
    assert wait_for(lambda: len(svc.chunks) == 5)
    session.stop()
    assert not tap._thread.is_alive() and tap.offer(object()) is False
    push(session, 5, first=5)
    time.sleep(0.1)
    assert len(svc.chunks) == 5
    session.stop()  # idempotent


def test_assembly_taps_a_started_extra_with_feed_and_closes_the_tap_before_the_extra(builders):
    log: list[str] = []
    svc = FeedService(log)
    plain = SyncService(log, "plain")
    builders["fake.music:build"] = lambda c, b: svc
    builders["fake.plain:build"] = lambda c, b: plain
    asm = assemble_fake({"music": "fake.music:build", "plain": "fake.plain:build"})
    asm.start()
    assert len(asm.session._taps) == 1  # only the extra that exposes feed
    tap = asm.session._taps[0]
    asm.shutdown()
    assert not tap._thread.is_alive() and "close:music" in log
    count = len(svc.chunks)
    tap.offer(object())
    time.sleep(0.05)
    assert len(svc.chunks) == count


def test_an_extra_whose_start_failed_is_not_tapped(builders):
    class BadStart(FeedService):
        def start(self) -> None:
            raise RuntimeError("no")

    svc = BadStart([])
    builders["fake.music:build"] = lambda c, b: svc
    asm = assemble_fake({"music": "fake.music:build"})
    asm.start()
    assert asm.session._taps == ()
    asm.shutdown()
