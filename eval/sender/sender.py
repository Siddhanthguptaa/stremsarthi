"""Optical-stamp stream sender: 1280x720@30 H.264 + AAC with a stamp and beep track.

Sends over SRT (mpegts) or RTMP (flv) to MediaMTX, or writes a file (``--no-realtime`` to render as fast as
possible). Adapted in spirit from IRL Shield ``relay/synthetic.py`` (MIT, see eval/LICENSES/): there the
stamp carried epoch ms and a frame index; here it carries a CRC-protected counter, monotonic capture time
and a beep flag (eval/sender/stamp.py), and the audio is a deterministic 1 kHz beep track.

    python -m eval.sender.sender --url srt://127.0.0.1:18890?streamid=publish:live \
        --script card_static:20,card_moving:20 --duration 60 --truth runs/x/truth.jsonl

Writes per-frame ground truth (targets, scene, capture time) to ``--truth`` as JSONL. No images, no audio,
no personal data: only counters, times and boxes of SPECIMEN targets.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

import av

from .beep import SAMPLE_RATE, BeepTrack, beep_frame_flag
from .scenes import ScenePlayer
from .stamp import CANVAS_H, CANVAS_W, draw_stamp, monotonic_ms

DEFAULT_SCRIPT = "card_static:20,card_moving:20,card_absent:20"


def container_format(url: str) -> str | None:
    if url.startswith("srt://") or url.startswith("udp://"):
        return "mpegts"
    if url.startswith("rtmp://") or url.startswith("rtmps://"):
        return "flv"
    return None  # let FFmpeg choose from the file extension


def _target_json(t: Any) -> dict[str, Any]:
    """One ground-truth target: category, bounding box, and corners when the target is rotated."""
    out: dict[str, Any] = {"category": t.category, "quad": list(t.quad)}
    if t.corners is not None:
        out["corners"] = [[round(x, 2), round(y, 2)] for x, y in t.corners]
    return out


class _PipeWriter:
    """File-like object for PyAV that writes and flushes straight into a subprocess stdin."""

    def __init__(self, stream: Any) -> None:
        self._s = stream

    def write(self, data: bytes) -> int:
        self._s.write(data)
        self._s.flush()
        return len(data)

    def flush(self) -> None:
        self._s.flush()


class _Output:
    """Output container plus, for SRT, the system ffmpeg that carries the MPEG-TS to the SRT socket.

    The FFmpeg inside the PyAV wheel has no libsrt, so for ``srt://`` PyAV muxes MPEG-TS into the stdin
    of ``ffmpeg -c copy -f mpegts srt://...`` (the system ffmpeg is built with libsrt).
    """

    def __init__(self, url: str) -> None:
        self.proc: subprocess.Popen | None = None
        if url.startswith("srt://"):
            self.proc = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-analyzeduration", "500000", "-probesize", "200000",
                 "-f", "mpegts", "-i", "pipe:0", "-c", "copy", "-f", "mpegts", url],
                stdin=subprocess.PIPE,
            )  # fmt: skip
            assert self.proc.stdin is not None
            self.container = av.open(
                _PipeWriter(self.proc.stdin), "w", format="mpegts", options={"flush_packets": "1"}
            )
        else:
            self.container = av.open(url, "w", format=container_format(url))

    def close(self) -> None:
        try:
            self.container.close()
        finally:
            if self.proc is not None:
                try:
                    if self.proc.stdin:
                        self.proc.stdin.close()
                except OSError:
                    pass
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()


def open_output(url: str) -> _Output:
    return _Output(url)


def run_sender(
    url: str,
    script: str = DEFAULT_SCRIPT,
    duration: float | None = None,
    fps: int = 30,
    crf: int = 23,
    preset: str = "veryfast",
    threads: int = 2,
    realtime: bool = True,
    truth_path: Path | None = None,
    stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Send frames until `duration` seconds (or the script's total when None) have been sent."""
    player = ScenePlayer(script, fps)
    total_frames = int(round((duration if duration is not None else player.total_s) * fps))
    beeps = BeepTrack(SAMPLE_RATE, fps)
    stop = stop or threading.Event()
    sink = open_output(url)
    out = sink.container
    vs = out.add_stream("libx264", rate=fps)
    vs.width, vs.height, vs.pix_fmt = CANVAS_W, CANVAS_H, "yuv420p"
    vs.time_base = Fraction(1, fps)
    vs.options = {
        "preset": preset,
        "tune": "zerolatency",
        "crf": str(crf),
        "g": str(fps),
        "bf": "0",
        "threads": str(threads),
    }
    astream = out.add_stream("aac", rate=SAMPLE_RATE, layout="mono")
    astream.bit_rate = 96_000
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE, frame_size=1024)

    truth_f = None
    if truth_path is not None:
        truth_path.parent.mkdir(parents=True, exist_ok=True)
        truth_f = truth_path.open("w", encoding="utf-8")
        truth_f.write(
            json.dumps(
                {
                    "type": "header",
                    "script": script,
                    "fps": fps,
                    "t0_monotonic": time.monotonic(),
                    "stamp": "eval/sender/stamp.py",
                }
            )
            + "\n"
        )

    origin = time.monotonic()
    late_max = 0.0
    sent = 0
    try:
        for index in range(total_frames):
            if stop.is_set():
                break
            if realtime:
                wait = origin + index / fps - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                else:
                    late_max = max(late_max, -wait)
            frame, scene, targets = player.render(index)
            cap_ms = monotonic_ms()
            flag = beep_frame_flag(index, fps)
            draw_stamp(frame, index, cap_ms, flag)
            vf = av.VideoFrame.from_ndarray(frame, format="rgb24").reformat(format="yuv420p")
            vf.pts, vf.time_base = index, Fraction(1, fps)
            for pkt in vs.encode(vf):
                out.mux(pkt)
            pcm = beeps.frame_samples(index).reshape(1, -1)
            af = av.AudioFrame.from_ndarray(pcm, format="flt", layout="mono")
            af.sample_rate = SAMPLE_RATE
            af.pts, af.time_base = index * beeps.samples_per_frame, Fraction(1, SAMPLE_RATE)
            for rf in resampler.resample(af):
                for pkt in astream.encode(rf):
                    out.mux(pkt)
            if truth_f is not None:
                truth_f.write(
                    json.dumps(
                        {
                            "type": "frame",
                            "seq": index,
                            "t_capture_ms": cap_ms,
                            "beep": flag,
                            "scene": scene,
                            "targets": [_target_json(t) for t in targets],
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
            sent += 1
        for pkt in vs.encode(None):
            out.mux(pkt)
        for rf in resampler.resample(None):
            for pkt in astream.encode(rf):
                out.mux(pkt)
        for pkt in astream.encode(None):
            out.mux(pkt)
    finally:
        try:
            sink.close()
        finally:
            if truth_f is not None:
                truth_f.close()
    return {
        "frames_sent": sent,
        "late_max_ms": round(late_max * 1000, 2),
        "elapsed_s": round(time.monotonic() - origin, 3),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--url", required=True, help="srt://host:port?streamid=publish:path | rtmp://host:port/path | file"
    )
    p.add_argument("--script", default=DEFAULT_SCRIPT, help="scene:seconds,... (default %(default)s)")
    p.add_argument(
        "--duration", type=float, default=None, help="seconds to send (default: the script length)"
    )
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--preset", default="veryfast")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--no-realtime", action="store_true", help="render as fast as possible (file output)")
    p.add_argument("--truth", type=Path, default=None, help="write per-frame ground truth JSONL here")
    p.add_argument("--clock", default="monotonic", choices=["monotonic"], help="stamp clock (only monotonic)")
    args = p.parse_args(argv)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stats = run_sender(
        args.url,
        args.script,
        args.duration,
        args.fps,
        args.crf,
        args.preset,
        args.threads,
        not args.no_realtime,
        args.truth,
        stop,
    )
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
