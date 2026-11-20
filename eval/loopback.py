"""Loopback smoke: stamp sender -> MediaMTX -> reader, plus an ffprobe receive check.

Pass criteria (docs/specs/eval.md gate 0): frames received >= 98 % of the frames sent over the window the
reader observed, 0 capture gaps > 100 ms. Delay measured here is the tap's own latency (the calibration
number that later phases subtract). Everything runs on localhost, CPU only, no GPU.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .harness.procs import CpuSampler, MediaMtx, find_mediamtx
from .sender.reader import read_stream

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "card_static:20,card_moving:20,card_absent:20"
MAX_START_LAG_FRAMES = 150  # reader must attach within 5 s of the sender starting


class EnvError(RuntimeError):
    """Something needed to measure is missing (exit code 2)."""


def _start_sender(url: str, duration_s: float, run_dir: Path, script: str = SCRIPT) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "eval.sender.sender",
        "--url",
        url,
        "--script",
        script,
        "--duration",
        str(duration_s),
        "--truth",
        str(run_dir / "truth.jsonl"),
    ]
    log = (run_dir / "sender.log").open("w")
    return subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)


def _count_truth_frames(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if '"type":"frame"' in line)


def _stop(proc: subprocess.Popen, grace_s: float = 5.0) -> int:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    return proc.returncode


def _tap_url(ep: Any, tap: str) -> str:
    return {"rtmp": ep.rtmp_read, "rtsp": ep.rtsp_read, "srt": ep.srt_read}[tap]("live")


def run_loopback(seconds: float, transport: str, run_dir: Path, tap: str = "rtmp") -> dict[str, Any]:
    """Send for `seconds` (reader window) through MediaMTX; returns metrics and pass/fail checks."""
    if find_mediamtx() is None:
        raise EnvError("MediaMTX binary not found (run scripts/fetch_tools.sh)")
    run_dir.mkdir(parents=True, exist_ok=True)
    with MediaMtx(run_dir) as mtx:
        ep = mtx.endpoints
        url = ep.srt_publish("live") if transport == "srt" else ep.rtmp_publish("live")
        sender = _start_sender(url, seconds + 10.0, run_dir)
        cpu = CpuSampler(sender.pid).start()
        try:
            res = read_stream(_tap_url(ep, tap), seconds, open_timeout_s=25.0)
        finally:
            cpu_stats = cpu.stop()
            sender_rc = _stop(sender)
        frames_sent = _count_truth_frames(run_dir / "truth.jsonl")
    summary = res.summary()
    first = summary["counter_first"]
    checks = {
        "received_fraction_ge_0.98": (summary["received_fraction"] or 0.0) >= 0.98,
        "capture_gaps_over_100ms_eq_0": summary["capture_gaps_over_100ms"] == 0,
        "reader_attached_within_5s": first is not None and first <= MAX_START_LAG_FRAMES,
        "stamp_unreadable_le_1pct": (summary["stamp_unreadable_frac"] or 0.0) <= 0.01,
        "no_reader_error": summary["error"] is None and summary["frames_read"] > 0,
    }
    out = {
        "transport": transport,
        "tap": tap,
        "reader_window_s": seconds,
        "frames_sent_total": frames_sent,
        "sender_exit": sender_rc,
        "sender_cpu": cpu_stats,
        "reader": summary,
        "checks": checks,
        "passed": all(checks.values()),
    }
    (run_dir / "loopback.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out


def ffprobe_check(transport: str, run_dir: Path, seconds: float = 6.0) -> dict[str, Any]:
    """Receive the sender's stream from MediaMTX with ffprobe and check codecs and geometry."""
    if find_mediamtx() is None:
        raise EnvError("MediaMTX binary not found (run scripts/fetch_tools.sh)")
    if shutil.which("ffprobe") is None:
        raise EnvError("ffprobe not on PATH")
    run_dir.mkdir(parents=True, exist_ok=True)
    with MediaMtx(run_dir) as mtx:
        ep = mtx.endpoints
        url = ep.srt_publish("live") if transport == "srt" else ep.rtmp_publish("live")
        sender = _start_sender(url, seconds + 10.0, run_dir, script="card_static:30")
        try:
            streams: list[dict[str, Any]] = []
            err = ""
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                p = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-rtsp_transport",
                        "tcp",
                        "-show_streams",
                        "-of",
                        "json",
                        ep.rtsp_read("live"),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if p.returncode == 0 and p.stdout.strip():
                    streams = json.loads(p.stdout).get("streams", [])
                    if len(streams) >= 2:
                        break
                err = p.stderr.strip()[-200:]
                time.sleep(0.7)
        finally:
            _stop(sender)
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    checks = {
        "video_h264": video.get("codec_name") == "h264",
        "video_1280x720": (video.get("width"), video.get("height")) == (1280, 720),
        "audio_aac": audio.get("codec_name") == "aac",
        "audio_48k": str(audio.get("sample_rate")) == "48000",
    }
    out = {
        "transport": transport,
        "video": {k: video.get(k) for k in ("codec_name", "width", "height", "r_frame_rate")},
        "audio": {k: audio.get(k) for k in ("codec_name", "sample_rate", "channels")},
        "checks": checks,
        "passed": all(checks.values()),
        "last_error": None if streams else err,
    }
    (run_dir / "ffprobe.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return out
