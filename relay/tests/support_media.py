"""Test helpers: free ports, FFmpeg publishers (testsrc2 + sine over SRT or RTMP), optical stamp.

Synthetic content only. Nothing here is imported by production code.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from relay.security import RelayAddress

STAMP_BITS = 32
STAMP_CELL = 40  # px; 32 cells x 40 px span the 1280 px canvas width


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def free_address() -> RelayAddress:
    ports: set[int] = set()
    while len(ports) < 3:
        ports.add(free_port())
    rtmp, rtsp, api = sorted(ports)
    return RelayAddress(
        "127.0.0.1", rtmp_port=rtmp, srt_port=free_port(socket.SOCK_DGRAM), rtsp_port=rtsp, api_port=api
    )


def wait_for(condition: Callable[[], Any], timeout: float, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    return condition()


def publish_testsrc(
    url: str,
    protocol: str,
    *,
    seconds: float | None = None,
    size: str = "1280x720",
    fps: int = 30,
    audio: bool = True,
) -> subprocess.Popen:
    """Start FFmpeg pushing real-time testsrc2 (+ 440 Hz sine) to ``url`` as ``rtmp`` or ``srt``."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", str(2 * fps), "-bf", "0"]
    cmd += ["-pix_fmt", "yuv420p", "-b:v", "4M", "-maxrate", "4M", "-bufsize", "2M"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
    if seconds is not None:
        cmd += ["-t", str(seconds)]
    cmd += ["-f", "flv" if protocol == "rtmp" else "mpegts", "-flush_packets", "1", url]
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def stop_process(proc: subprocess.Popen, *, kill: bool = False) -> None:
    if proc.poll() is None:
        proc.kill() if kill else proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    if proc.stderr is not None:
        proc.stderr.close()


# --------------------------------------------------------------------------- optical stamp
def draw_stamp(image: np.ndarray, value: int) -> None:
    """Draw a 32-bit value as black/white cells along the top edge (survives x264)."""
    for bit in range(STAMP_BITS):
        shade = 255 if (value >> (STAMP_BITS - 1 - bit)) & 1 else 0
        image[0:STAMP_CELL, bit * STAMP_CELL : (bit + 1) * STAMP_CELL] = shade


def read_stamp(image: np.ndarray) -> int:
    value = 0
    half = STAMP_CELL // 2
    for bit in range(STAMP_BITS):
        cell = image[half - 4 : half + 4, bit * STAMP_CELL + half - 4 : bit * STAMP_CELL + half + 4]
        value = (value << 1) | int(cell.mean() > 127)
    return value


def now_stamp() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def stamp_latency_s(stamp: int) -> float:
    return ((now_stamp() - stamp) & 0xFFFFFFFF) / 1000.0


class StampedPublisher:
    """Real-time raw video with a time stamp in every frame, encoded by FFmpeg and pushed to ``url``."""

    def __init__(self, url: str, protocol: str, *, fps: int = 30, audio: bool = True):
        self.fps = fps
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        cmd = cmd[:-1]  # stdin carries the frames
        cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "1280x720", "-framerate", str(fps), "-i", "-"]
        if audio:
            cmd += ["-re", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-g",
            str(2 * fps),
            "-bf",
            "0",
        ]
        cmd += ["-pix_fmt", "yuv420p", "-b:v", "3M"]
        if audio:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
        cmd += ["-f", "flv" if protocol == "rtmp" else "mpegts"]
        cmd += ["-flush_packets", "1", url]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        base = np.full((720, 1280, 3), 96, np.uint8)
        base[:, :, 1] = np.linspace(40, 160, 1280, dtype=np.uint8)[None, :]
        interval = 1.0 / self.fps
        next_t = time.monotonic()
        while not self._stop.is_set():
            frame = base.copy()
            draw_stamp(frame, now_stamp())
            try:
                self._proc.stdin.write(frame.tobytes())
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return
            next_t += interval
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass
        stop_process(self._proc)


# --------------------------------------------------------------------------- offline media files
def write_nut(
    path: str,
    *,
    n_frames: int = 30,
    size: tuple[int, int] = (160, 90),
    fps: int = 25,
    start_s: float = 1.0,
    audio: bool = True,
) -> None:
    """Write a small NUT file (raw video + PCM) whose timestamps start at ``start_s`` seconds.

    Video frame ``i`` is a flat shade of ``40 + i``; audio is a ramp so sample indices are checkable.
    """
    from fractions import Fraction

    import av

    width, height = size
    with av.open(path, "w", format="nut") as container:
        video = container.add_stream("rawvideo", rate=fps)
        video.width, video.height, video.pix_fmt = width, height, "bgr24"
        video.time_base = video.codec_context.time_base = Fraction(1, 90000)
        sound = None
        if audio:
            sound = container.add_stream("pcm_s16le", rate=48000)
            sound.layout = "mono"
            sound.time_base = sound.codec_context.time_base = Fraction(1, 48000)
        per_frame = 48000 // fps
        for i in range(n_frames):
            if sound is not None:
                samples = (np.arange(per_frame, dtype=np.int32) + i * per_frame) % 20000
                chunk = av.AudioFrame.from_ndarray(
                    samples.astype(np.int16).reshape(1, -1), format="s16", layout="mono"
                )
                chunk.sample_rate = 48000
                chunk.pts, chunk.time_base = round(start_s * 48000) + i * per_frame, Fraction(1, 48000)
                for packet in sound.encode(chunk):
                    container.mux(packet)
            image = np.full((height, width, 3), 40 + i, np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            frame.pts, frame.time_base = round((start_s + i / fps) * 90000), Fraction(1, 90000)
            for packet in video.encode(frame):
                container.mux(packet)
        for stream in (video, sound):
            if stream is not None:
                for packet in stream.encode():
                    container.mux(packet)


# --------------------------------------------------------------------------- collectors
class FrameRecord:
    """Light copy of a frame: timing only, plus the image for the first few frames (RAM stays small)."""

    __slots__ = ("arrival", "generation", "image", "probe", "pts", "seq")

    def __init__(self, frame: Any, keep_image: bool, probe: Callable[[Any], Any] | None):
        self.seq, self.pts, self.arrival = frame.seq, frame.pts, frame.arrival
        self.generation = getattr(frame, "generation", 0)
        self.image = frame.image if keep_image else None
        self.probe = probe(frame.image) if probe is not None else None


class Collector:
    """Drains ``frames()`` and ``audio_blocks()`` of a source into lists on two threads.

    Frames are stored as ``FrameRecord``: a 60 s run at 720p would otherwise hold about 5 GB.
    """

    def __init__(self, source: Any, *, keep_images: int = 12, probe: Callable[[Any], Any] | None = None):
        self.source = source
        self.keep_images, self.probe = keep_images, probe
        self.frames: list[FrameRecord] = []
        self.audio: list[Any] = []
        self._threads = [
            threading.Thread(target=self._drain_frames, daemon=True),
            threading.Thread(target=self._drain_audio, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _drain_frames(self) -> None:
        for frame in self.source.frames():
            self.frames.append(FrameRecord(frame, len(self.frames) < self.keep_images, self.probe))

    def _drain_audio(self) -> None:
        for block in self.source.audio_blocks():
            self.audio.append(block)

    def stop(self) -> None:
        self.source.close()
        for thread in self._threads:
            thread.join(timeout=3)


class TimedBus:
    """``InProcessBus`` that also records ``time.monotonic()`` for every published event."""

    def __init__(self) -> None:
        from contracts.fakes.bus import InProcessBus

        self.bus = InProcessBus()
        self.timed: list[tuple[float, Any, Any]] = []

    def publish(self, topic: Any, event: Any) -> None:
        self.timed.append((time.monotonic(), topic, event))
        self.bus.publish(topic, event)

    def subscribe(self, topics: Any) -> Any:
        return self.bus.subscribe(topics)

    def of(self, topic: Any) -> list[Any]:
        return self.bus.of(topic)

    def times_of(self, topic: Any) -> list[tuple[float, Any]]:
        return [(t, e) for t, tp, e in self.timed if tp == topic]


def kill_stamped(publisher: StampedPublisher) -> None:
    """SIGKILL the encoder (no goodbye, like a phone losing signal) and stop the feeder thread."""
    import signal

    publisher._proc.send_signal(signal.SIGKILL)
    publisher.stop()


def publish_flash_beep(url: str, protocol: str, *, seconds: float) -> subprocess.Popen:
    """Black 1280x720@30 that flashes white for 0.2 s every 2 s, with a 1 kHz beep on the same beat."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re"]
    cmd += ["-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30,geq=lum='255*lt(mod(T,2),0.2)':cb=128:cr=128"]
    cmd += ["-re", "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*1000*t)*lt(mod(t,2),0.2)':s=48000"]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-g",
        "60",
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
    ]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-t", str(seconds)]
    cmd += ["-f", "flv" if protocol == "rtmp" else "mpegts", "-flush_packets", "1", url]
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
