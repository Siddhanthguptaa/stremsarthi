"""Stream reader: pulls a tap (RTSP/RTMP/SRT/file), decodes stamps and beeps, measures delay.

Per decoded video frame: stamp decode (CRC checked; a bad CRC counts as ``stamp_unreadable``),
``t_read = time.monotonic()`` right after decode, delay = t_read - stamp capture time (same host, same
CLOCK_MONOTONIC as the sender). Audio: beep onsets by short-time RMS crossing -30 dBFS (5 ms hop).
A/V offset per beep = audio onset time - video flag frame time, both on the stream's PTS timeline.

Only counters and times are kept: no pixels, no audio, no text leave this module.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import av
import numpy as np

from .beep import BeepDetector
from .stamp import MS_MODULUS, read_stamp


@dataclass
class FrameRecord:
    seq: int
    stamp_ms: int
    t_read: float  # monotonic seconds
    delay_s: float
    pts_s: float | None
    beep: bool


@dataclass
class ReaderResult:
    frames: list[FrameRecord] = field(default_factory=list)
    video_decoded: int = 0
    stamp_unreadable: int = 0
    slate_frames: int = 0  # unreadable stamp but a hook recognised a slate (PROTECT/HOLD): not an error
    audio_onsets_s: list[float] = field(default_factory=list)
    beep_frame_pts_s: list[float] = field(default_factory=list)
    started_monotonic: float = 0.0
    ended_monotonic: float = 0.0
    open_attempts: int = 0
    error: str | None = None
    decode_ms: list[float] = field(default_factory=list)

    @property
    def elapsed_s(self) -> float:
        return max(0.0, self.ended_monotonic - self.started_monotonic)

    def summary(self) -> dict[str, Any]:
        from ..harness import metrics  # local import keeps the reader importable on its own

        delays = [f.delay_s for f in self.frames]
        seqs = [f.seq for f in self.frames]
        counters = metrics.counter_continuity(seqs)
        capture_gaps = metrics.capture_gaps([f.stamp_ms for f in self.frames], MS_MODULUS)
        arrivals = [f.t_read for f in self.frames]
        inter = np.diff(arrivals) if len(arrivals) > 1 else np.array([0.0])
        offsets = metrics.av_offsets_ms(self.audio_onsets_s, self.beep_frame_pts_s)
        return {
            "video_decoded": self.video_decoded,
            "frames_read": len(self.frames),
            "stamp_unreadable": self.stamp_unreadable,
            "slate_frames": self.slate_frames,
            "stamp_unreadable_frac": self.stamp_unreadable / self.video_decoded
            if self.video_decoded
            else None,
            "elapsed_s": round(self.elapsed_s, 3),
            "fps": round(self.video_decoded / self.elapsed_s, 3) if self.elapsed_s > 0 else None,
            "counter_first": seqs[0] if seqs else None,
            "counter_last": seqs[-1] if seqs else None,
            "received_fraction": counters["received_fraction"],
            "missing_frames": counters["missing"],
            "max_capture_gap_ms": capture_gaps["max_gap_ms"],
            "capture_gaps_over_100ms": capture_gaps["n_over_100ms"],
            "max_interarrival_ms": round(float(inter.max()) * 1000, 2),
            "delay": metrics.delay_percentiles(delays),
            "beeps_audio": len(self.audio_onsets_s),
            "beeps_video": len(self.beep_frame_pts_s),
            "av_offset_ms": offsets,
            "stamp_decode_ms_p95": round(float(np.percentile(self.decode_ms, 95)), 3)
            if self.decode_ms
            else None,
            "error": self.error,
        }


#: called per decoded video frame with (av frame, decoded Stamp or None, monotonic read time); may return
#: "slate" for an unreadable-stamp frame it recognises as a PROTECT/HOLD slate
VideoHook = Callable[[Any, Any, float], str | None]
#: called per decoded audio frame with (mono float samples in [-1, 1], stream pts seconds, monotonic read time)
AudioHook = Callable[[np.ndarray, float, float], None]


def _open_input(url: str, options: dict[str, str] | None) -> tuple[Any, subprocess.Popen | None]:
    """Open a tap. RTSP and files go straight through PyAV; ``srt://`` goes through the system ffmpeg
    (the PyAV wheel has no libsrt) which hands PyAV an MPEG-TS pipe. RTMP is opened without options:
    in this PyAV build any option dict on an RTMP input segfaults."""
    if url.startswith("srt://"):
        proc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-analyzeduration", "500000",
             "-probesize", "200000", "-i", url, "-c", "copy", "-f", "mpegts", "-flush_packets", "1",
             "-muxdelay", "0", "-muxpreload", "0", "pipe:1"],
            stdout=subprocess.PIPE,
        )  # fmt: skip
        assert proc.stdout is not None
        try:
            return av.open(
                proc.stdout,
                mode="r",
                format="mpegts",
                options={"analyzeduration": "500000", "probesize": "200000"},
            ), proc
        except (av.error.FFmpegError, OSError):
            proc.kill()
            proc.wait(timeout=5)
            raise
    if url.startswith("rtmp://") or url.startswith("rtmps://"):
        return av.open(url, mode="r", timeout=(5.0, 10.0)), None
    opts = {"fflags": "nobuffer", "flags": "low_delay", "analyzeduration": "1000000", "probesize": "1000000"}
    if url.startswith("rtsp://"):
        opts["rtsp_transport"] = "tcp"
    opts.update(options or {})
    return av.open(url, mode="r", options=opts, timeout=(5.0, 10.0)), None


def read_stream(
    url: str,
    seconds: float,
    stop: threading.Event | None = None,
    open_timeout_s: float = 20.0,
    options: dict[str, str] | None = None,
    video_hook: VideoHook | None = None,
    audio_hook: AudioHook | None = None,
) -> ReaderResult:
    """Read `url` for `seconds` of wall time (after the stream opens), retrying the open for up to
    `open_timeout_s` while the publisher is not there yet."""
    stop = stop or threading.Event()
    res = ReaderResult(started_monotonic=time.monotonic())

    container = None
    feeder: subprocess.Popen | None = None
    deadline = time.monotonic() + open_timeout_s
    while container is None and not stop.is_set():
        res.open_attempts += 1
        try:
            container, feeder = _open_input(url, options)
        except (av.error.FFmpegError, OSError) as exc:
            if time.monotonic() > deadline:
                res.error = f"could not open {url.split('?')[0]}: {exc}"
                res.ended_monotonic = time.monotonic()
                return res
            time.sleep(0.5)
    if container is None:
        res.ended_monotonic = time.monotonic()
        return res

    res.started_monotonic = time.monotonic()
    vstream = container.streams.video[0] if container.streams.video else None
    astream = container.streams.audio[0] if container.streams.audio else None
    streams = [s for s in (vstream, astream) if s is not None]
    detector = BeepDetector(astream.rate) if astream is not None else None
    try:
        for packet in container.demux(*streams):
            if stop.is_set() or time.monotonic() - res.started_monotonic >= seconds:
                break
            for frame in packet.decode():
                if isinstance(frame, av.VideoFrame):
                    t0 = time.perf_counter()
                    luma = frame.to_ndarray(format="gray")
                    stamp = read_stamp(luma)
                    t_read = time.monotonic()
                    res.decode_ms.append((time.perf_counter() - t0) * 1000.0)
                    res.video_decoded += 1
                    verdict = video_hook(frame, stamp, t_read) if video_hook is not None else None
                    if stamp is None:
                        if verdict == "slate":
                            res.slate_frames += 1
                        else:
                            res.stamp_unreadable += 1
                        continue
                    pts_s = float(frame.pts * frame.time_base) if frame.pts is not None else None
                    delay = ((t_read * 1000.0) - stamp.capture_ms) % MS_MODULUS / 1000.0
                    res.frames.append(
                        FrameRecord(stamp.counter, stamp.capture_ms, t_read, delay, pts_s, stamp.beep)
                    )
                    if stamp.beep and pts_s is not None:
                        res.beep_frame_pts_s.append(pts_s)
                elif detector is not None and frame.pts is not None:
                    pcm = frame.to_ndarray()
                    mono = (
                        pcm.astype(np.float32).reshape(-1)
                        if pcm.ndim == 1
                        else pcm.astype(np.float32).mean(axis=0)
                    )
                    if pcm.dtype.kind in "iu":
                        mono = mono / float(np.iinfo(pcm.dtype).max)
                    detector.feed(mono, float(frame.pts * frame.time_base))
                    if audio_hook is not None:
                        audio_hook(mono, float(frame.pts * frame.time_base), time.monotonic())
    except (av.error.FFmpegError, OSError) as exc:
        res.error = f"read error: {exc}"
    finally:
        res.ended_monotonic = time.monotonic()
        try:
            container.close()
        except Exception:  # noqa: BLE001 - closing a broken network input can raise anything
            pass
        if feeder is not None:
            feeder.kill()
            feeder.wait(timeout=5)
    if detector is not None:
        res.audio_onsets_s = list(detector.onsets)
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = p.parse_args(argv)
    res = read_stream(args.url, args.seconds)
    summary = res.summary()
    print(json.dumps(summary, indent=2) if args.json else summary)
    return 0 if res.error is None and res.frames else 1


if __name__ == "__main__":
    sys.exit(main())
