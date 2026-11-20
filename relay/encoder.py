"""The killable encoder: one spawned process encodes H.264 and AAC once; destinations only mux.

Adapted from the encoder half of IRL Shield ``relay/stream.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``): spawn-context worker over a private socketpair, one frame in flight, a 5 s reply
deadline, the parent kills and restarts it with backoff, and only fixed error codes cross the seam.
Changes for Stream Sarthi:

* frames travel through shared memory, not pickles (one slot, because one frame is in flight);
* x264 ``veryfast`` is the default and NVENC is chosen only when it works (``select_encoder``);
* the worker returns encoded packets instead of muxing; ``relay/destinations.py`` fans them out to N
  independent muxers, so a slow or dead destination never touches the encoder or the others;
* audio is aligned to the video PTS (``AudioAligner``): a gap is filled with silence and an overlap
  trimmed, so audio and video stay in sync even when the single-slot writer skipped a frame;
* nothing is written to disk (no archive, no manifest).
"""

from __future__ import annotations

import ctypes
import io
import logging
import multiprocessing
import os
import pickle
import random
import shutil
import socket
import struct
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import numpy as np

try:  # Linux only; the relay runs in WSL2/Linux.
    import resource
except ImportError:  # pragma: no cover
    resource = None  # type: ignore[assignment]

log = logging.getLogger("relay.encoder")

AUDIO_RATE = 48_000
MAX_AUDIO_SAMPLES = AUDIO_RATE * 6  # the shared audio slot: a frame's audio plus any carried or padded audio
REPLY_DEADLINE_S = 5.0
START_DEADLINE_S = 20.0
BACKOFF_SECONDS = (0.5, 1.0, 2.0, 4.0, 8.0)
KIND_VIDEO, KIND_AUDIO = 0, 1
CODEC_X264, CODEC_NVENC = "libx264", "h264_nvenc"
CODECS = (CODEC_X264, CODEC_NVENC)
STAGE_ENCODE = "encode"
GPU_LOCK_PATH = "/tmp/streamsarthi-gpu.lock"
ENV_GPU_LOCK_HELD = "STREAMSARTHI_GPU_LOCK_HELD"
_MAX_MESSAGE = 64 * 1024 * 1024


class EncoderError(RuntimeError):
    """Only fixed codes cross the transport seam, never libav text (it can contain URLs)."""

    def __init__(self, code: str, detail: str | None = None):
        self.code, self.detail = code, detail  # detail: an exception class name from the worker, never text
        super().__init__(code if detail is None else f"{code}:{detail}")


# --------------------------------------------------------------------------------------- settings
@dataclass(frozen=True, slots=True)
class EncoderSettings:
    """What the encoder produces. ``codec`` is ``libx264`` or ``h264_nvenc``."""

    codec: str = CODEC_X264
    width: int = 1280
    height: int = 720
    fps: int = 30
    video_kbps: int = 4500
    audio_kbps: int = 128
    keyframe_seconds: float = 2.0
    threads: int = 4  # x264 worker threads (0 = the encoder's choice, one per core: too greedy beside the engine)

    def __post_init__(self) -> None:
        if self.codec not in CODECS:
            raise ValueError("unsupported encoder codec")
        if self.width < 2 or self.height < 2 or self.width % 2 or self.height % 2:
            raise ValueError("encoder dimensions must be even")
        if not 1 <= self.fps <= 120 or self.video_kbps < 100 or self.audio_kbps < 16:
            raise ValueError("invalid encoder rates")
        if not 0 < self.keyframe_seconds <= 10:
            raise ValueError("invalid keyframe interval")

    @property
    def gop(self) -> int:
        return max(1, round(self.keyframe_seconds * self.fps))

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3

    def with_codec(self, codec: str) -> EncoderSettings:
        return EncoderSettings(
            codec, self.width, self.height, self.fps, self.video_kbps, self.audio_kbps,
            self.keyframe_seconds, self.threads,
        )  # fmt: skip

    @classmethod
    def from_config(cls, config: Any, codec: str = CODEC_X264) -> EncoderSettings:
        width, height = config.canvas
        relay = config.relay
        return cls(
            codec=codec, width=int(width), height=int(height), fps=int(config.fps),
            video_kbps=int(relay.video_bitrate_kbps), audio_kbps=int(relay.audio_bitrate_kbps),
            keyframe_seconds=float(relay.keyframe_seconds),
        )  # fmt: skip


def video_options(settings: EncoderSettings) -> dict[str, str]:
    """Codec options for the video encoder. Constant-ish rate, closed GOP, no B-frames, no lookahead."""
    kbps, gop = settings.video_kbps, settings.gop
    common = {"maxrate": f"{kbps}k", "bufsize": f"{2 * kbps}k", "profile": "high"}
    if settings.codec == CODEC_NVENC:
        options = {
            "preset": "p4", "tune": "ll", "rc": "cbr", "zerolatency": "1", "forced-idr": "1",
            "b_ref_mode": "disabled", **common,
        }  # fmt: skip
    else:
        # scenecut=0 and min-keyint=keyint make every keyframe land exactly on the 2 s grid.
        options = {
            "preset": "veryfast", "tune": "zerolatency",
            "x264-params": f"keyint={gop}:min-keyint={gop}:scenecut=0:bframes=0:rc-lookahead=0",
            **common,
        }  # fmt: skip
        if settings.threads:
            options["threads"] = str(settings.threads)
    return options


def describe_command(settings: EncoderSettings, destinations: Sequence[str] = ()) -> str:
    """The equivalent ``ffmpeg`` command line, safe to log: destination URLs are placeholders.

    The real encoder is PyAV (no argv, so no key in the process table). This string exists so a log,
    a test or an operator can see exactly what is produced without any secret.
    """
    video = [
        "-c:v", settings.codec, "-pix_fmt", "yuv420p", "-r", str(settings.fps), "-g", str(settings.gop),
        "-bf", "0", "-b:v", f"{settings.video_kbps}k",
    ]  # fmt: skip
    for key, value in video_options(settings).items():
        video += [f"-{key}", value]
    audio = ["-c:a", "aac", "-b:a", f"{settings.audio_kbps}k", "-ar", str(AUDIO_RATE), "-ac", "2"]
    inputs = [
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{settings.width}x{settings.height}", "-r", str(settings.fps),
        "-i", "pipe:video", "-f", "s16le", "-ar", str(AUDIO_RATE), "-ac", "1", "-i", "pipe:audio",
    ]  # fmt: skip
    outputs: list[str] = []
    for name in destinations or ("<destination>",):
        outputs += ["-f", "flv", f"rtmp://127.0.0.1:<port>/<app>/<redacted:{name}>"]
    return " ".join(["ffmpeg", *inputs, *video, *audio, *outputs])


def nvenc_available(
    *,
    runner: Callable[..., Any] = subprocess.run,
    ffmpeg: str = "ffmpeg",
    lock_path: str = GPU_LOCK_PATH,
    timeout: float = 40.0,
) -> bool:
    """True when ``ffmpeg -encoders`` lists ``h264_nvenc`` and a one-frame test encode works.

    The test encode is a GPU run: it takes the shared GPU lock unless the caller already holds it
    (``STREAMSARTHI_GPU_LOCK_HELD=1``, set by ``scripts/run_engine.sh`` and the GPU test wrapper).
    """
    try:
        listing = runner([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15)
        if CODEC_NVENC not in (listing.stdout or ""):
            return False
        test = [
            ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30",
            "-frames:v", "1", "-c:v", CODEC_NVENC, "-f", "null", "-",
        ]  # fmt: skip
        if os.environ.get(ENV_GPU_LOCK_HELD) != "1" and shutil.which("flock"):
            test = ["flock", "-w", "30", lock_path, *test]
        return runner(test, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def select_encoder(
    preference: str, gpu_enabled: bool, *, probe: Callable[[], bool] = nvenc_available
) -> tuple[str, str]:
    """Pick ``(codec, reason)``. ``auto`` uses NVENC only when the GPU is enabled and the probe passes."""
    if preference == "x264":
        return CODEC_X264, "configured"
    if preference == "nvenc":
        return (CODEC_NVENC, "configured") if probe() else (CODEC_X264, "nvenc_unavailable")
    if gpu_enabled and probe():
        return CODEC_NVENC, "auto_nvenc"
    return CODEC_X264, "auto_x264" if not gpu_enabled else "nvenc_unavailable"


# --------------------------------------------------------------------------------- packets, timing
@dataclass(frozen=True, slots=True)
class EncodedPacket:
    """One encoded packet. Timestamps are in ``time_base(kind)``: 1/fps for video, 1/48000 for audio."""

    kind: int
    data: bytes
    pts: int
    dts: int
    duration: int
    keyframe: bool

    @property
    def is_video(self) -> bool:
        return self.kind == KIND_VIDEO


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """What a muxer needs to join this encode: the worker's FLV header plus the picture geometry.

    The header-only template has no picture size and reports the FLV 44.1 kHz default; the muxer
    fixes both from this record.
    """

    header: bytes
    width: int
    height: int
    fps: int
    video_extradata: bytes = b""  # the encoder's own (Annex B) SPS/PPS: tells the FLV muxer to convert packets


def time_base(kind: int, fps: int) -> Fraction:
    return Fraction(1, fps) if kind == KIND_VIDEO else Fraction(1, AUDIO_RATE)


@dataclass(frozen=True, slots=True)
class EncodeResult:
    generation: int
    video_index: int
    pts: float
    packets: tuple[EncodedPacket, ...]
    encode_s: float


class AudioAligner:
    """Places mono 48 kHz audio on the video clock.

    Audio for a frame at engine time ``pts`` starts at sample ``round((pts - t0) * 48000)``. Normal
    frames are contiguous and pass through. A gap larger than ``tolerance_s`` (a frame skipped by the
    single-slot writer, or a stall) is filled with silence; an overlap is trimmed. Small jitter is
    left alone, so a steady source never gets clicks. A gap beyond ``max_gap_s`` raises, which restarts
    the timeline (new encoder generation).
    """

    def __init__(self, rate: int = AUDIO_RATE, tolerance_s: float = 0.02, max_gap_s: float = 4.0):
        self.rate = rate
        self.tolerance = int(tolerance_s * rate)
        self.max_gap = int(max_gap_s * rate)
        self.t0: float | None = None
        self.next_sample = 0
        self.padded_samples = 0
        self.trimmed_samples = 0

    def reset(self) -> None:
        self.t0, self.next_sample = None, 0

    def place(self, pts: float, pcm: Any) -> tuple[int, np.ndarray]:
        """Return ``(start_sample, int16 samples)`` for this chunk, timeline-aligned."""
        samples = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if self.t0 is None:
            self.t0, self.next_sample = float(pts), 0
        gap = round((float(pts) - self.t0) * self.rate) - self.next_sample
        if gap > self.max_gap:
            raise EncoderError("timeline-gap")
        start = self.next_sample
        if gap > self.tolerance:
            samples = np.concatenate([np.zeros(gap, np.int16), samples])
            self.padded_samples += gap
        elif gap < -self.tolerance:
            cut = min(-gap, len(samples))
            samples = samples[cut:]
            self.trimmed_samples += cut
        self.next_sample = start + len(samples)
        return start, samples


# ----------------------------------------------------------------------------------- the transport
def _message(channel: socket.socket, value: Any) -> None:
    data = pickle.dumps(value, protocol=5)
    channel.sendall(struct.pack("!I", len(data)) + data)


def _receive(channel: socket.socket, timeout: float | None = None) -> Any:
    # This socketpair is private to our parent and child process, never a network listener.
    deadline = None if timeout is None else time.monotonic() + timeout

    def read(size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                channel.settimeout(remaining)
            chunk = channel.recv(size - len(data))
            if not chunk:
                raise EOFError
            data.extend(chunk)
        return bytes(data)

    (size,) = struct.unpack("!I", read(4))
    if size > _MAX_MESSAGE:
        raise ValueError("encoder-message-size")
    return pickle.loads(read(size))  # noqa: S301 - private socketpair, see above


class _MemorySink(io.RawIOBase):
    """The worker muxes the FLV header into memory once, so the parent can build muxer templates."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        self.buffer += bytes(data)
        return len(data)


def _encode_worker(channel: socket.socket, settings: EncoderSettings, video_mem: Any, audio_mem: Any) -> None:
    """Runs in the spawned process. Never logs: any libav text could carry a URL."""
    import av  # imported here so the parent's import of this module stays light

    if resource is not None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    av.logging.set_level(None)
    container: Any = None
    try:
        sink = _MemorySink()
        container = av.open(sink, "w", format="flv", options={"flvflags": "no_duration_filesize"})
        video = container.add_stream(settings.codec, rate=settings.fps, options=video_options(settings))
        video.width, video.height, video.pix_fmt = settings.width, settings.height, "yuv420p"
        video.bit_rate = settings.video_kbps * 1000
        context = video.codec_context
        context.gop_size, context.max_b_frames = settings.gop, 0
        context.time_base = Fraction(1, settings.fps)
        try:  # tag BT.709 (HD): the swscale conversion below writes the same matrix
            context.colorspace, context.color_primaries, context.color_trc = 1, 1, 1
        except Exception:  # noqa: BLE001 - tagging is cosmetic
            pass
        audio = container.add_stream("aac", rate=AUDIO_RATE)
        audio.layout = "stereo"
        audio.bit_rate = settings.audio_kbps * 1000
        audio.codec_context.time_base = Fraction(1, AUDIO_RATE)
        container.start_encoding()
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=AUDIO_RATE)
        image = np.frombuffer(video_mem, dtype=np.uint8).reshape(settings.height, settings.width, 3)
        samples = np.frombuffer(audio_mem, dtype=np.int16)
        _message(channel, ("ok", {"header": bytes(sink.buffer), "extradata": bytes(context.extradata or b"")}))
        while True:
            command, value = _receive(channel)
            if command == "close":
                _message(channel, ("ok", None))
                return
            video_index, audio_items = value
            packets: list[tuple[Any, ...]] = []
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            frame = frame.reformat(format="yuv420p", dst_colorspace="ITU709")
            frame.pts, frame.time_base = video_index, Fraction(1, settings.fps)
            for packet in video.encode(frame):
                packets.append((KIND_VIDEO, bytes(packet), packet.pts, packet.dts, packet.duration or 0,
                                bool(packet.is_keyframe)))  # fmt: skip
            offset = 0
            for start, count in audio_items:
                mono = samples[offset : offset + count]
                offset += count
                stereo = np.repeat(mono[:, None], 2, axis=1).reshape(1, -1)
                sound = av.AudioFrame.from_ndarray(stereo, format="s16", layout="stereo")
                sound.sample_rate, sound.pts, sound.time_base = AUDIO_RATE, start, Fraction(1, AUDIO_RATE)
                for converted in resampler.resample(sound):
                    for packet in audio.encode(converted):
                        packets.append((KIND_AUDIO, bytes(packet), packet.pts, packet.dts, packet.duration or 0,
                                        False))  # fmt: skip
            _message(channel, ("ok", packets))
    except (EOFError, ConnectionError):
        pass  # the parent closed the channel: a normal stop
    except BaseException as error:  # noqa: BLE001
        if os.environ.get("STREAMSARTHI_RELAY_DEBUG"):  # the worker never handles a URL, so this is safe
            import traceback

            traceback.print_exc()
        try:
            _message(channel, ("error", {"error_class": type(error).__name__}))
        except Exception:  # noqa: BLE001 - the parent is gone; nobody to tell
            pass
    finally:
        if container is not None:
            try:
                container.close()
            except Exception:  # noqa: BLE001
                pass
        channel.close()


class EncoderProcess:
    """One spawned encoder: ``start()``, ``encode()`` (one frame in flight) and ``stop()`` (kills it)."""

    def __init__(
        self,
        settings: EncoderSettings,
        *,
        reply_deadline: float = REPLY_DEADLINE_S,
        start_deadline: float = START_DEADLINE_S,
    ):
        self.settings = settings
        self.reply_deadline, self.start_deadline = reply_deadline, start_deadline
        self.header = b""
        self.video_extradata = b""
        self._process: Any = None
        self._channel: socket.socket | None = None
        self._video: Any = None
        self._audio: Any = None
        self._video_view: np.ndarray | None = None
        self._audio_view: np.ndarray | None = None

    @property
    def pid(self) -> int | None:
        return getattr(self._process, "pid", None)

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self) -> bytes:
        ctx = multiprocessing.get_context("spawn")
        self._video = ctx.RawArray(ctypes.c_ubyte, self.settings.frame_bytes)
        self._audio = ctx.RawArray(ctypes.c_int16, MAX_AUDIO_SAMPLES)
        self._video_view = np.frombuffer(self._video, dtype=np.uint8).reshape(
            self.settings.height, self.settings.width, 3
        )
        self._audio_view = np.frombuffer(self._audio, dtype=np.int16)
        parent, child = socket.socketpair()
        self._channel = parent
        self._process = ctx.Process(
            target=_encode_worker, args=(child, self.settings, self._video, self._audio), name="relay-encoder"
        )
        try:
            self._process.start()
        except BaseException:
            self._cleanup()
            raise EncoderError("encoder-startup") from None
        finally:
            child.close()
        status, details = self._reply(self.start_deadline, "encoder-startup")
        self.header = bytes(details["header"])
        self.video_extradata = bytes(details["extradata"])
        return self.header

    def _reply(self, deadline: float, code: str) -> tuple[str, Any]:
        assert self._channel is not None
        try:
            status, details = _receive(self._channel, timeout=deadline)
        except TimeoutError:
            self.stop()
            raise EncoderError(code + "-timeout") from None
        except Exception:  # noqa: BLE001 - EOF, reset, bad message: the worker is gone
            self.stop()
            raise EncoderError(code) from None
        if status != "ok":
            self.stop()
            raise EncoderError(code, str(details.get("error_class")) if isinstance(details, dict) else None)
        return status, details

    def encode(self, video_index: int, image: np.ndarray, audio_items: Sequence[tuple[int, np.ndarray]]) -> list[EncodedPacket]:
        if self._channel is None or not self.alive:
            raise EncoderError("encoder-exited")
        assert self._video_view is not None and self._audio_view is not None
        np.copyto(self._video_view, image)
        offset, items = 0, []
        for start, samples in audio_items:
            count = len(samples)
            if offset + count > MAX_AUDIO_SAMPLES:
                raise EncoderError("audio-overflow")
            self._audio_view[offset : offset + count] = samples
            items.append((int(start), count))
            offset += count
        try:
            self._channel.settimeout(self.reply_deadline)
            _message(self._channel, ("frame", (int(video_index), items)))
        except Exception:  # noqa: BLE001
            self.stop()
            raise EncoderError("encoder-write") from None
        _, packets = self._reply(self.reply_deadline, "encoder-write")
        return [EncodedPacket(*packet) for packet in packets]

    def stop(self) -> None:
        """Kill the worker (SIGTERM, then SIGKILL). Idempotent."""
        process, channel = self._process, self._channel
        self._process = self._channel = None
        if channel is not None:
            try:
                channel.close()
            except OSError:
                pass
        if process is not None:
            try:
                process.join(timeout=0.3)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                if not process.is_alive():
                    process.close()
            except Exception:  # noqa: BLE001
                pass
        self._video_view = self._audio_view = None
        self._video = self._audio = None

    _cleanup = stop

    def kill(self) -> None:
        """Test and watchdog hook: SIGKILL without a goodbye (the parent sees an exit on the next frame)."""
        if self._process is not None and self._process.pid:
            try:
                os.kill(self._process.pid, 9)
            except OSError:
                pass


class Encoder:
    """The resilient encoder: restarts the worker with backoff and falls back from NVENC to x264.

    ``encode()`` runs on the writer thread and returns ``None`` while the encoder is down or backing
    off, so a frame is dropped instead of waiting. Each successful start is a new ``generation``: the
    timeline (video index and audio sample clock) restarts, and destinations reconnect.
    """

    def __init__(
        self,
        settings: EncoderSettings,
        *,
        factory: Callable[[EncoderSettings], Any] = EncoderProcess,
        clock: Callable[[], float] = time.monotonic,
        backoff: Sequence[float] = BACKOFF_SECONDS,
        jitter: float = 0.2,
        on_event: Callable[[str], None] | None = None,
    ):
        self.settings = settings
        self._factory, self._clock = factory, clock
        self._backoff, self._jitter = tuple(backoff), jitter
        self._on_event = on_event
        self._process: Any = None
        self._aligner = AudioAligner()
        self._t0: float | None = None
        self._last_index = -1
        self._attempt = 0
        self._retry_at = 0.0
        self._closed = False
        self.generation = 0
        self.restarts = 0
        self.errors: list[str] = []
        self.last_error: str | None = None
        self.header = b""
        self.fell_back = False

    # -- state
    @property
    def codec(self) -> str:
        return self.settings.codec

    @property
    def healthy(self) -> bool:
        return not self._closed and self._process is not None and self._process.alive

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def stream_info(self) -> StreamInfo:
        s = self.settings
        extradata = getattr(self._process, "video_extradata", b"")
        return StreamInfo(self.header, s.width, s.height, s.fps, bytes(extradata))

    def _event(self, code: str) -> None:
        if self._on_event is not None:
            try:
                self._on_event(code)
            except Exception:  # noqa: BLE001
                log.debug("encoder event callback failed", exc_info=True)

    # -- lifecycle
    def start(self) -> Encoder:
        """Start the first worker, blocking. Raises ``EncoderError`` if no codec can start."""
        self._start()
        return self

    def _start(self) -> None:
        candidates = [self.settings]
        if self.settings.codec == CODEC_NVENC:
            candidates.append(self.settings.with_codec(CODEC_X264))
        error: EncoderError | None = None
        for settings in candidates:
            process = self._factory(settings)
            try:
                header = process.start()
            except EncoderError as failure:
                error = failure
                process.stop()
                if settings.codec == CODEC_NVENC:
                    self._event("encoder_nvenc_failed")
                continue
            if settings is not self.settings:
                self.settings, self.fell_back = settings, True
                self._event("encoder_fallback_x264")
            self._process, self.header = process, header
            self.generation += 1
            self._aligner.reset()
            self._t0, self._last_index = None, -1
            return
        assert error is not None
        raise error

    def _fail(self, code: str) -> None:
        self.last_error = code
        self.errors.append(code)
        del self.errors[:-16]
        if self._process is not None:
            self._process.stop()
            self._process = None
        self._attempt += 1
        delay = self._backoff[min(self._attempt - 1, len(self._backoff) - 1)]
        self._retry_at = self._clock() + delay * (1.0 + random.uniform(0.0, self._jitter))
        self._event("encoder_down")

    def encode(self, pts: float, image: np.ndarray, chunks: Sequence[tuple[float, np.ndarray]]) -> EncodeResult | None:
        """Encode one frame plus its audio chunks. ``None`` means the frame was dropped (encoder down)."""
        if self._closed:
            raise EncoderError("encoder-closed")
        if self._process is None:
            if self._clock() < self._retry_at:
                return None
            try:
                self._start()
            except EncoderError as error:
                self._fail(error.code)
                return None
            self.restarts += 1
            self._attempt = 0
            self._event("encoder_up")
        began = self._clock()
        try:
            if self._t0 is None:
                self._t0 = float(pts)
            index = max(self._last_index + 1, round((float(pts) - self._t0) * self.settings.fps))
            items = [self._aligner.place(chunk_pts, samples) for chunk_pts, samples in chunks]
            items = [(start, samples) for start, samples in items if len(samples)]
            packets = self._process.encode(index, image, items)
        except EncoderError as error:
            self._fail(error.code)
            return None
        self._last_index = index
        return EncodeResult(self.generation, index, float(pts), tuple(packets), self._clock() - began)

    def kill_worker(self) -> None:
        """Test hook: SIGKILL the worker; the next ``encode`` notices, backs off and restarts."""
        if self._process is not None:
            self._process.kill()

    def close(self) -> None:
        self._closed = True
        if self._process is not None:
            self._process.stop()
            self._process = None


def cpu_seconds(pid: int) -> float | None:
    """Total user+system CPU time of a process from ``/proc`` (None if unavailable)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        ticks = int(fields[11]) + int(fields[12])
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


__all__ = [
    "AUDIO_RATE", "AudioAligner", "CODEC_NVENC", "CODEC_X264", "EncodeResult", "EncodedPacket", "Encoder",
    "EncoderError", "EncoderProcess", "EncoderSettings", "KIND_AUDIO", "KIND_VIDEO", "STAGE_ENCODE", "StreamInfo",
    "cpu_seconds", "describe_command","nvenc_available", "select_encoder", "time_base",
    "video_options",
]  # fmt: skip
