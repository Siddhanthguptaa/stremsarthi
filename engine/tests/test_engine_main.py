"""Assembler: module loading from ``runtime.modules``, failure cleanup, and clean SIGINT/SIGTERM shutdown."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import zmq

from contracts import Config, Topic, loads
from engine import main as engine_main
from engine import session_fakes
from engine.main import ModuleLoadError, assemble, load_builder, run

ROOT = Path(engine_main.__file__).resolve().parents[1]
FAKES = {
    "relay": "engine.session_fakes:build_relay",
    "vision": "engine.session_fakes:build_vision",
    "audio": "engine.session_fakes:build_audio",
}


def fake_config(**kw) -> Config:
    return Config(_env_file=None, delay_seconds=3.0, runtime={"modules": FAKES, "extras": {}}, **kw)


# ------------------------------------------------------------------ loading


def test_load_builder_resolves_a_dotted_path():
    assert load_builder("engine.session_fakes:build_relay") is session_fakes.build_relay


@pytest.mark.parametrize(
    "path",
    [
        "",
        "no_colon",
        ":build",
        "engine.session_fakes:",
        "engine.nope_not_here:build",
        "engine.session_fakes:missing",
        "engine.session_fakes:FAKE_FRAMES",
    ],
)
def test_bad_paths_raise_module_load_error(path):
    with pytest.raises(ModuleLoadError):
        load_builder(path)


def test_assemble_wires_source_engine_sink_without_importing_other_modules():
    before = {
        name for name in sys.modules if name.split(".")[0] in ("relay", "vision", "audio", "guard", "chat")
    }
    asm = assemble(fake_config(), broker=False)
    try:
        assert asm.lane_host is not None and asm.audio_host is not None and asm.broker is None
        assert asm.session.source is asm.endpoints.source and asm.session.sink is asm.endpoints.sink
        assert asm.session.lane_host is asm.lane_host and asm.session.audio_host is asm.audio_host
        assert asm.session.engine.lanes == tuple(asm.lane_host.active_lanes)
    finally:
        asm.shutdown()
    # the engine itself never imports those packages (only the modules named in config would)
    assert before == {
        name for name in sys.modules if name.split(".")[0] in ("relay", "vision", "audio", "guard", "chat")
    }


def test_a_missing_module_is_an_error_not_a_silent_pass_through():
    cfg = Config(_env_file=None, runtime={"modules": {**FAKES, "vision": "engine.nothing:build"}})
    with pytest.raises(ModuleLoadError, match="cannot import"):
        assemble(cfg, broker=False)


def test_wrong_return_types_are_rejected_and_built_hosts_are_closed():
    closed = []

    class Host(session_fakes.FakeAudioHost):
        def close(self):
            closed.append(True)

    session_fakes.build_audio_probe = lambda config, bus: Host()  # type: ignore[attr-defined]
    try:
        modules = {
            **FAKES,
            "audio": "engine.session_fakes:build_audio_probe",
            "relay": "engine.session_fakes:build_audio",
        }
        with pytest.raises(ModuleLoadError, match="RelayEndpoints"):
            assemble(Config(_env_file=None, runtime={"modules": modules}), broker=False)
        assert closed  # the audio host built before the failure was closed again
    finally:
        del session_fakes.build_audio_probe  # type: ignore[attr-defined]


def test_skip_is_explicit_and_logged(caplog):
    with caplog.at_level("WARNING", logger="streamsarthi.engine"):
        asm = assemble(fake_config(), skip=["audio"], broker=False)
    try:
        assert asm.audio_host is None and asm.session.audio_host is None
        assert any("spoken PII is NOT checked" in r.message for r in caplog.records)
    finally:
        asm.shutdown()
    with pytest.raises(ModuleLoadError):
        assemble(fake_config(), skip=["nonsense"], broker=False)


def test_a_failed_assembly_releases_the_broker_endpoints():
    d = tempfile.mkdtemp(prefix="ssm")
    xpub, xsub = f"ipc://{d}/out", f"ipc://{d}/in"
    try:
        cfg = Config(_env_file=None, runtime={"modules": {**FAKES, "relay": "engine.nothing:build"}})
        with pytest.raises(ModuleLoadError):
            assemble(cfg, xpub_endpoint=xpub, xsub_endpoint=xsub)
        asm = assemble(fake_config(), xpub_endpoint=xpub, xsub_endpoint=xsub)  # the endpoints are free again
        asm.shutdown()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ a full run in this process


def test_full_run_over_ipc_emits_status_and_exits_when_the_source_ends(monkeypatch):
    monkeypatch.setattr(session_fakes, "FAKE_FRAMES", 30)  # 1 s of video: about 4.2 s through a 3.0 s delay
    d = tempfile.mkdtemp(prefix="ssm")
    xpub, xsub = f"ipc://{d}/out", f"ipc://{d}/in"
    timing = Path(d) / "t.jsonl"
    cfg = fake_config(engine={"timing_log_path": str(timing)})
    asm = assemble(cfg, xpub_endpoint=xpub, xsub_endpoint=xsub)
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.LINGER, 0)
    sub.setsockopt(zmq.SUBSCRIBE, b"engine.status")
    sub.setsockopt(zmq.SUBSCRIBE, b"safety.event")
    sub.connect(xpub)
    stop = threading.Event()
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(run(asm, stop)))
    began = time.monotonic()
    time.sleep(0.2)
    thread.start()
    seen: dict[str, list] = {"engine.status": [], "safety.event": []}
    try:
        while thread.is_alive() and time.monotonic() - began < 15:
            if sub.poll(100):
                frames = sub.recv_multipart()
                seen[frames[0].decode()].append(loads(frames[-1]))
        thread.join(5)
    finally:
        stop.set()
        thread.join(5)
        sub.close(0)
        ctx.term()
    sink = asm.endpoints.sink
    assert result == [0] and not thread.is_alive()
    assert len(sink.records) == 30 and asm.session.counts["LIVE"] == 30
    assert asm.lane_host.closed and asm.audio_host.closed and sink.closed
    assert seen["engine.status"] and any(s.state == "live" for s in seen["engine.status"])
    assert any(e.action == "masked" for e in seen["safety.event"])
    lines = [json.loads(line) for line in timing.read_text().splitlines()]
    assert sum(1 for line in lines if line["stage"] == "compose") == 30
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ the real entry point, in a subprocess


def _spawn(*extra: str, env: dict[str, str] | None = None) -> subprocess.Popen[str]:
    args = [sys.executable, "-m", "engine.main", "--profile", "dev", "--no-broker"]
    for name, path in FAKES.items():
        args += ["--module", f"{name}={path}"]
    return subprocess.Popen(
        [*args, *extra],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_for_line(proc: subprocess.Popen[str], needle: str, timeout: float) -> list[str]:
    lines: list[str] = []
    found = threading.Event()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            if needle in line:
                found.set()

    threading.Thread(target=reader, daemon=True).start()
    assert found.wait(timeout), "".join(lines)
    return lines


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signals_stop_the_engine_cleanly(sig, tmp_path):
    path = tmp_path / "timing.jsonl"
    proc = _spawn(env={"ENGINE__TIMING_LOG_PATH": str(path), "STREAM_DELAY_SECONDS": "3.0"})
    try:
        _wait_for_line(proc, "engine ready", 20)
        time.sleep(4.0)  # past the 3.0 s delay: frames are flowing
        proc.send_signal(sig)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(line["stage"] == "compose" for line in lines)  # the log was flushed on shutdown


def test_a_broken_module_path_exits_with_code_2():
    args = [sys.executable, "-m", "engine.main", "--no-broker", "--module", "vision=engine.nothing:build"]
    out = subprocess.run(
        args,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 2 and "cannot start" in out.stderr


def test_topics_used_by_the_engine_exist():
    assert {Topic.HOLD, Topic.CONFIG, Topic.ENGINE_STATUS, Topic.SAFETY_EVENT, Topic.TIMING} <= set(Topic)
