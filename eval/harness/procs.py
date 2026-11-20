"""Process helpers for the harness: MediaMTX loopback, process-tree CPU sampling, port picking.

The MediaMTX used here is a private instance on random localhost ports with a generated config
(SRT, RTMP and RTSP only; no HLS, WebRTC, API or playback), so a smoke run never collides with a
developer's own relay. The binary is found under ``.tmp/tools/mediamtx-v*/`` (T0.1) or ``SS_MEDIAMTX``.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[2]


def find_mediamtx(root: Path = ROOT) -> Path | None:
    env = os.environ.get("SS_MEDIAMTX")
    if env and Path(env).is_file():
        return Path(env)
    for base in (root / ".tmp" / "tools", root / ".tmp" / "eval-tools"):
        for cand in sorted(base.glob("mediamtx-v*/mediamtx"), reverse=True):
            if cand.is_file() and os.access(cand, os.X_OK):
                return cand
    return None


def free_port(kind: str = "tcp") -> int:
    sock_type = socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, sock_type) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_port(port: int, timeout_s: float = 10.0) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


@dataclass
class Endpoints:
    rtsp_port: int
    rtmp_port: int
    srt_port: int

    def srt_publish(self, path: str = "live") -> str:
        return f"srt://127.0.0.1:{self.srt_port}?streamid=publish:{path}&pkt_size=1316"

    def rtmp_publish(self, path: str = "live") -> str:
        return f"rtmp://127.0.0.1:{self.rtmp_port}/{path}"

    def rtmp_read(self, path: str = "live") -> str:
        return f"rtmp://127.0.0.1:{self.rtmp_port}/{path}"

    def rtsp_read(self, path: str = "live") -> str:
        return f"rtsp://127.0.0.1:{self.rtsp_port}/{path}"

    def srt_read(self, path: str = "live") -> str:
        return f"srt://127.0.0.1:{self.srt_port}?streamid=read:{path}"


_CONFIG = """\
logLevel: warn
readTimeout: 10s
writeTimeout: 10s
api: false
metrics: false
pprof: false
playback: false
hls: false
webrtc: false
moq: false
rtsp: true
rtspTransports: [tcp]
rtspAddress: 127.0.0.1:{rtsp}
rtmp: true
rtmpAddress: 127.0.0.1:{rtmp}
srt: true
srtAddress: 127.0.0.1:{srt}
paths:
  all_others:
"""


class MediaMtx:
    """Context manager: ``with MediaMtx(run_dir) as mtx: mtx.endpoints...``"""

    def __init__(self, run_dir: Path, binary: Path | None = None) -> None:
        self.run_dir = Path(run_dir)
        self.binary = binary or find_mediamtx()
        self.endpoints = Endpoints(free_port(), free_port(), free_port("udp"))
        self.proc: subprocess.Popen | None = None
        self._log = None

    def __enter__(self) -> MediaMtx:
        if self.binary is None:
            raise FileNotFoundError("MediaMTX not found: run scripts/fetch_tools.sh (or set SS_MEDIAMTX)")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        cfg = self.run_dir / "mediamtx.yml"
        cfg.write_text(
            _CONFIG.format(
                rtsp=self.endpoints.rtsp_port, rtmp=self.endpoints.rtmp_port, srt=self.endpoints.srt_port
            )
        )
        self._log = (self.run_dir / "mediamtx.log").open("w")
        self.proc = subprocess.Popen(
            [str(self.binary), str(cfg)], stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True
        )
        if not wait_port(self.endpoints.rtsp_port, 10.0) or self.proc.poll() is not None:
            self.stop()
            raise RuntimeError(f"MediaMTX did not start; see {self.run_dir / 'mediamtx.log'}")
        return self

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self._log is not None:
            self._log.close()
            self._log = None

    def __exit__(self, *exc: object) -> None:
        self.stop()


class CpuSampler:
    """Samples the CPU use of a process tree at 1 Hz: average and peak cores."""

    def __init__(self, pid: int, interval_s: float = 1.0) -> None:
        self.pid = pid
        self.interval_s = interval_s
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _tree(self) -> list[psutil.Process]:
        try:
            root = psutil.Process(self.pid)
            return [root, *root.children(recursive=True)]
        except psutil.Error:
            return []

    def _cpu_seconds(self) -> float:
        total = 0.0
        for p in self._tree():
            try:
                t = p.cpu_times()
                total += t.user + t.system
            except psutil.Error:
                continue
        return total

    def _run(self) -> None:
        last_cpu, last_t = self._cpu_seconds(), time.monotonic()
        while not self._stop.wait(self.interval_s):
            cpu, now = self._cpu_seconds(), time.monotonic()
            if now > last_t:
                self.samples.append(max(0.0, (cpu - last_cpu) / (now - last_t)))
            last_cpu, last_t = cpu, now

    def start(self) -> CpuSampler:
        self._thread.start()
        return self

    def stop(self) -> dict[str, float]:
        self._stop.set()
        self._thread.join(timeout=3)
        if not self.samples:
            return {"mean_cores": 0.0, "peak_cores": 0.0, "n": 0}
        return {
            "mean_cores": round(sum(self.samples) / len(self.samples), 3),
            "peak_cores": round(max(self.samples), 3),
            "n": len(self.samples),
        }
