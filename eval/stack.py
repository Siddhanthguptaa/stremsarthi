"""Run the sender -> engine -> tap chain once and collect what the gate needs.

``engine="real"`` starts ``python -m engine.main --profile dev`` (which starts MediaMTX through the relay's
supervisor, loads the vision and audio hosts and publishes the ``shielded`` tap). ``engine="ref"`` and
``"ref-broken"`` start a private MediaMTX plus ``eval.refengine`` instead: a stand-in used to validate the
harness and to show the gate fails on a build without the fail-safe.

Nothing here reads ``.env`` or prints a credential. The real engine is configured only through environment
variables (the ``Config`` env source): ports, a fixed ingest login, the timing log path, the delay, the GPU
switch, ``SS_FAULT`` and ``DESTINATIONS`` (tap only, unless ``live_youtube`` is set and the key is in the
environment). A GPU chain runs under ``flock /tmp/streamsarthi-gpu.lock``.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .harness.gpu_lock import LOCK_PATH
from .harness.procs import CpuSampler, MediaMtx, find_mediamtx, free_port
from .holdbus import DEFAULT_XPUB_ENDPOINT, DEFAULT_XSUB_ENDPOINT
from .tap import Tap, TapResult

ROOT = Path(__file__).resolve().parents[1]
#: set by scripts/gate_phase1.sh, which already holds `flock /tmp/streamsarthi-gpu.lock` around a GPU leg
#: (a nested flock from the engine child would wait on its own parent forever)
ENV_LOCK_HELD = "SS_GPU_LOCK_HELD"


class EnvError(RuntimeError):
    """Something needed to run is missing (gate exit code 2)."""


@dataclass
class Action:
    """Something to do at ``at_s`` seconds after the first live frame reaches the tap."""

    at_s: float
    name: str
    fn: Callable[[], Any]


@dataclass
class ChainSpec:
    run_dir: Path
    script: str
    duration_s: float
    engine: str = "real"  # real | ref | ref-broken
    transport: str = "srt"
    delay_s: float = 4.0
    gpu: bool = False
    fault: str | None = None
    ocr: bool = True
    ocr_every_s: float = 1.0
    live_youtube: bool = False
    bus_in: str = DEFAULT_XSUB_ENDPOINT
    bus_out: str = DEFAULT_XPUB_ENDPOINT
    tail_s: float | None = None  # seconds to keep reading after the sender ends (default delay + 4)
    actions: list[Action] = field(default_factory=list)
    engine_ready_timeout_s: float = 90.0


@dataclass
class ChainResult:
    spec: ChainSpec
    tap: TapResult
    sender: dict[str, Any]
    cpu: dict[str, float]
    vram_mb: dict[str, float | None]
    events: list[tuple[str, float, str | None]]
    engine_exit: int | None
    engine_timing_path: Path
    fault_log_path: Path
    tap_url_redacted: str
    t_first_live: float | None


# ---------------------------------------------------------------- small helpers


def _wait_http(url: str, timeout_s: float, alive: Callable[[], bool]) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if not alive():
            return False
        try:
            with urlopen(url, timeout=1.0) as r:  # noqa: S310 - loopback only
                if r.status == 200:
                    return True
        except (URLError, OSError, ValueError):
            pass
        time.sleep(0.3)
    return False


def _terminate(proc: subprocess.Popen | None, grace_s: float = 10.0) -> int | None:
    if proc is None:
        return None
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=grace_s)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    return proc.returncode


def gpu_present() -> bool:
    if shutil.which("nvidia-smi") is None:
        return False
    r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, check=False, timeout=15)
    return r.returncode == 0 and "GPU" in r.stdout


class VramSampler:
    """nvidia-smi ``memory.used`` at 1 Hz. It is a query, not a GPU job, so it does not take the GPU lock
    (the chain itself holds it)."""

    def __init__(self, interval_s: float = 1.0) -> None:
        self.interval_s = interval_s
        self.samples: list[float] = []
        self.baseline: float | None = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    @staticmethod
    def _used() -> float | None:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=False, timeout=5,
            )  # fmt: skip
            return float(r.stdout.strip().splitlines()[0]) if r.returncode == 0 and r.stdout.strip() else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            v = self._used()
            if v is not None:
                self.samples.append(v)

    def start(self) -> VramSampler:
        self.baseline = self._used()
        self._t.start()
        return self

    def stop(self) -> dict[str, float | None]:
        self._stop.set()
        if self._t.is_alive():  # never started when the chain failed before the engine came up
            self._t.join(timeout=3)
        peak = max(self.samples) if self.samples else None
        return {
            "baseline_mb": self.baseline,
            "peak_mb": peak,
            "peak_over_baseline_mb": None if peak is None or self.baseline is None else peak - self.baseline,
        }


def _real_engine_env(
    spec: ChainSpec, ports: dict[str, int], creds: tuple[str, str], timing: Path, fault_log: Path
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "INGEST_BIND_HOST": "127.0.0.1",
            "INGEST_PORT_RTMP": str(ports["rtmp"]),
            "INGEST_PORT_SRT": str(ports["srt"]),
            "INGEST_PORT_RTSP": str(ports["rtsp"]),
            "RELAY__MEDIAMTX_API_PORT": str(ports["api"]),
            "INGEST_PUBLISH_USER": creds[0],
            "INGEST_PUBLISH_PASSWORD": creds[1],
            "ENGINE__TIMING_LOG_PATH": str(timing),
            "DELAY_SECONDS": str(spec.delay_s),
            "GPU__ENABLED": "true" if spec.gpu else "false",
            "GPU__ORT_PROVIDER": "cuda" if spec.gpu else "cpu",
            "SS_FAULT_LOG": str(fault_log),
            "PYTHONUNBUFFERED": "1",
        }
    )
    if spec.fault:
        env["SS_FAULT"] = spec.fault
    else:
        env.pop("SS_FAULT", None)
    if spec.live_youtube:
        if not env.get("YOUTUBE_STREAM_KEY"):
            raise EnvError("live_youtube needs YOUTUBE_STREAM_KEY in the environment")
    else:
        # never stream to a platform during a measurement run, whatever .env holds
        env.pop("YOUTUBE_STREAM_KEY", None)
        env.pop("TWITCH_STREAM_KEY", None)
        env["DESTINATIONS"] = json.dumps([{"kind": "mediamtx_tap", "enabled": True}])
    return env


def _sender_url(transport: str, ports: dict[str, int], creds: tuple[str, str] | None, path: str) -> str:
    if transport == "srt":
        sid = f"publish:{path}" + (f":{creds[0]}:{creds[1]}" if creds else "")
        return f"srt://127.0.0.1:{ports['srt']}?streamid={sid}&pkt_size=1316&mode=caller&latency=120000"
    auth = f"?user={creds[0]}&pass={creds[1]}" if creds else ""
    return f"rtmp://127.0.0.1:{ports['rtmp']}/{path}{auth}"


# ---------------------------------------------------------------- the chain


def run_chain(spec: ChainSpec, on_engine_start: Callable[[int], None] | None = None) -> ChainResult:
    """Run one chain. Raises ``EnvError`` when it cannot start (nothing was measured).

    ``on_engine_start(pid)`` is called once the engine is up (the soak driver samples its memory)."""
    run_dir = spec.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    timing = run_dir / "engine-timing.jsonl"
    fault_log = run_dir / "fault.jsonl"
    for p in (timing, fault_log):
        p.unlink(missing_ok=True)
    events: list[tuple[str, float, str | None]] = []
    procs: list[subprocess.Popen] = []
    mtx: MediaMtx | None = None
    engine: subprocess.Popen | None = None
    sender: subprocess.Popen | None = None
    logs: list[Any] = []
    vram = VramSampler() if spec.gpu else None
    cpu: CpuSampler | None = None
    try:
        # ---- bring up the engine (real) or the reference stand-in
        if spec.engine == "real":
            if not (ROOT / "engine" / "main.py").exists() or not (ROOT / "relay" / "runtime.py").exists():
                raise EnvError("engine/ or relay/ is missing: cannot run the real chain")
            ports = {"rtmp": free_port(), "srt": free_port("udp"), "rtsp": free_port(), "api": free_port()}
            creds = ("evalpub", secrets.token_urlsafe(24))
            env = _real_engine_env(spec, ports, creds, timing, fault_log)
            cmd = [
                sys.executable,
                "-m",
                "engine.main",
                "--profile",
                "dev",
                "--xpub",
                spec.bus_out,
                "--xsub",
                spec.bus_in,
            ]
            if not (ROOT / "audio" / "orchestrator.py").exists():
                # gate 1 is about video and the pipe: until the audio host exists, run the engine's own fake
                # (a pass-through host, no mute spans) instead of failing to load the module
                cmd += ["--module", "audio=engine.session_fakes:build_audio"]
            if spec.gpu and not os.environ.get(ENV_LOCK_HELD):
                cmd = ["flock", LOCK_PATH, *cmd]  # the gate script may already hold the lock for us
            elog = (run_dir / "engine.log").open("w")
            logs.append(elog)
            if vram:
                vram.start()
            engine = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=elog, stderr=subprocess.STDOUT, start_new_session=True
            )
            procs.append(engine)
            api_url = f"http://127.0.0.1:{ports['api']}/v3/paths/list"
            ready = _wait_http(
                api_url, spec.engine_ready_timeout_s, lambda: engine is not None and engine.poll() is None
            )
            if not ready:
                tail = "\n".join((run_dir / "engine.log").read_text(errors="ignore").splitlines()[-8:])
                raise EnvError(f"engine did not come up (exit {engine.poll()}); last log lines:\n{tail}")
            # MediaMTX answers long before vision has loaded its models (CUDA init alone takes tens of seconds):
            # start the sender only once the engine says it is ready, or the first seconds are measured cold
            ready_by = time.monotonic() + spec.engine_ready_timeout_s
            elog_path = run_dir / "engine.log"
            while time.monotonic() < ready_by and engine.poll() is None:
                if "engine ready" in elog_path.read_text(errors="ignore"):
                    break
                time.sleep(0.25)
            else:
                tail = "\n".join(elog_path.read_text(errors="ignore").splitlines()[-8:])
                raise EnvError(f"engine never reported ready (exit {engine.poll()}); last log lines:\n{tail}")
            publish = _sender_url(spec.transport, ports, creds, "camera")
            tap_url = f"rtmp://127.0.0.1:{ports['rtmp']}/shielded"
            cpu = CpuSampler(engine.pid).start()
            if on_engine_start:
                on_engine_start(engine.pid)
        else:
            if find_mediamtx() is None:
                raise EnvError("MediaMTX binary not found (run scripts/fetch_tools.sh)")
            try:
                mtx = MediaMtx(run_dir).__enter__()
            except (
                RuntimeError
            ) as exc:  # a MediaMTX that cannot start is an environment error, not a failed criterion
                raise EnvError(str(exc)) from exc
            ep = mtx.endpoints
            ports = {"rtmp": ep.rtmp_port, "srt": ep.srt_port, "rtsp": ep.rtsp_port, "api": 0}
            elog = (run_dir / "engine.log").open("w")
            logs.append(elog)
            env = dict(os.environ, SS_FAULT_LOG=str(fault_log), PYTHONUNBUFFERED="1")
            if spec.fault:
                env["SS_FAULT"] = spec.fault
            else:
                env.pop("SS_FAULT", None)
            cmd = [
                sys.executable, "-m", "eval.refengine",
                "--in-url", ep.rtmp_read("camera"), "--out-url", ep.rtmp_publish("shielded"),
                "--script", spec.script, "--delay", str(spec.delay_s),
                "--timing-log", str(timing), "--bus", spec.bus_in,
            ]  # fmt: skip
            if spec.engine == "ref-broken":
                cmd.append("--broken")
            engine = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=elog, stderr=subprocess.STDOUT, start_new_session=True
            )
            procs.append(engine)
            publish = _sender_url(spec.transport, ports, None, "camera")
            tap_url = ep.rtmp_read("shielded")
            cpu = CpuSampler(engine.pid).start()
            if on_engine_start:
                on_engine_start(engine.pid)
            time.sleep(0.5)  # the SUB socket binds before the sender starts

        # ---- sender
        sender_log = (run_dir / "sender.log").open("w")
        logs.append(sender_log)
        sender = subprocess.Popen(
            [sys.executable, "-m", "eval.sender.sender", "--url", publish, "--script", spec.script,
             "--duration", str(spec.duration_s), "--truth", str(run_dir / "truth.jsonl")],
            cwd=ROOT, stdout=sender_log, stderr=subprocess.STDOUT, start_new_session=True,
        )  # fmt: skip
        procs.append(sender)
        t_sender = time.monotonic()

        # ---- tap on its own thread, actions on another
        tail = spec.tail_s if spec.tail_s is not None else spec.delay_s + 4.0
        window = spec.duration_s + spec.delay_s + tail + 30.0
        stop = threading.Event()
        tap = Tap(tap_url, spec.script, ocr=spec.ocr, ocr_every_s=spec.ocr_every_s)
        holder: dict[str, TapResult] = {}
        tt = threading.Thread(target=lambda: holder.update(r=tap.run(window, stop, 60.0)), name="tap")
        tt.start()

        t_first_live: float | None = None
        pending = sorted(spec.actions, key=lambda a: a.at_s)
        end_at = t_sender + spec.duration_s + spec.delay_s + tail
        while time.monotonic() < end_at and tt.is_alive():
            if t_first_live is None:
                for f in list(tap.frames):
                    if f.kind == "live":
                        t_first_live = f.t_read
                        events.append(("first_live", f.t_read, None))
                        break
            while pending and t_first_live is not None and time.monotonic() >= t_first_live + pending[0].at_s:
                act = pending.pop(0)
                t_act = time.monotonic()
                try:
                    act.fn()
                    events.append((act.name, t_act, None))
                except Exception as exc:  # the action's failure is data, not a crash
                    events.append((act.name + "_failed", t_act, type(exc).__name__))
            if sender.poll() is not None and sender.returncode not in (0, None) and t_first_live is None:
                raise EnvError(f"sender exited with {sender.returncode} before any frame reached the tap")
            time.sleep(0.05)
        stop.set()
        tt.join(timeout=60)
        result = holder.get("r")
        if result is None:
            raise EnvError("tap produced no result")
        sender_stats: dict[str, Any] = {"exit": sender.poll()}
        return ChainResult(
            spec, result, sender_stats, cpu.stop() if cpu else {}, vram.stop() if vram else {},
            events, engine.poll() if engine else None, timing, fault_log,
            "shielded", t_first_live,
        )  # fmt: skip
    finally:
        for p in reversed(procs):
            _terminate(p)
        if mtx is not None:
            mtx.stop()
        for fh in logs:
            try:
                fh.close()
            except OSError:
                pass


def result_json(r: ChainResult) -> dict[str, Any]:
    """Small JSON view of a chain result for logs (no frames, no URLs)."""
    return {
        "engine": r.spec.engine,
        "script": r.spec.script,
        "duration_s": r.spec.duration_s,
        "engine_exit": r.engine_exit,
        "cpu": r.cpu,
        "vram_mb": r.vram_mb,
        "events": [{"name": n, "t": round(t, 3), "detail": d} for n, t, d in r.events],
        "reader": r.tap.reader.summary(),
        "errors": r.tap.errors,
    }
