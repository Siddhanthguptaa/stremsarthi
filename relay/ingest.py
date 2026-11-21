"""Ingest: a PyAV reader of the loopback MediaMTX path, implementing ``contracts.runtime.FrameSource``.

Adapted from the input half of IRL Shield ``relay/stream.py`` (``NetworkInput``; MIT, Copyright (c)
2026 Elan Nash; see ``relay/LICENSE``). Kept: the reader thread with reconnect generations, the
bounded drop-oldest queue that counts what it drops, fixed failure codes instead of libav text,
and credentials held in memory only. Changed: frames are letterboxed to the canonical 1280x720
canvas (portrait and 4:3 input is padded, never stretched); PTS is rebased to 0 at the first frame
of each connection generation; audio becomes 48 kHz mono int16 blocks of 20 ms tagged with the
absolute sample index on that same clock; the frame rate is read from the stream instead of being
hard-coded; an ingest stall of more than ``hold_after_s`` publishes ``HoldCommand(AUTO_TRANSPORT)``
and the first good frame afterwards releases it; every decoded frame emits an ``ingest_decode``
``StageTiming``.

Generations: a reconnect starts a new generation. ``IngestFrame.generation`` and
``AudioBlock.generation`` carry it, ``NetworkInput.generation`` exposes the latest one, and both the
PTS clock and the audio sample index restart at 0. The engine restarts its clock and starts in
PROTECT when the generation changes (contract note: ``Frame`` itself has no such field).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import av
import numpy as np

from contracts.enums import Category, HoldSource, Modality, Topic
from contracts.events import HoldCommand, SafetyEvent, StageTiming
from contracts.geometry import CANVAS_H, CANVAS_W
from contracts.media import Frame

log = logging.getLogger("relay.ingest")

AUDIO_RATE = 48_000
AUDIO_BLOCK_SAMPLES = 960  # 20 ms at 48 kHz
HOLD_ACTOR = "relay_ingest"
STAGE_INGEST_DECODE = "ingest_decode"
# MediaMTX supervisor event codes that prove the phone link (or the relay) is gone.
TRANSPORT_LOSS_CODES = frozenset({"publisher-disconnected", "mediamtx-exit"})


@dataclass(frozen=True, slots=True)
class IngestFrame(Frame):
    """``Frame`` plus the connection generation it belongs to (additive, still a ``Frame``)."""

    generation: int = 0


class AudioBlock(tuple):
    """``(absolute_sample_index, int16 mono 48 kHz ndarray)`` with a ``generation`` attribute.

    It unpacks as the two-tuple that ``FrameSource.audio_blocks`` promises.
    """

    generation: int

    def __new__(cls, index: int, pcm: Any, generation: int = 0) -> AudioBlock:
        obj = super().__new__(cls, (index, pcm))
        obj.generation = generation
        return obj


# --------------------------------------------------------------------------- letterbox
def letterbox_geometry(
    src_w: int, src_h: int, dst_w: int = CANVAS_W, dst_h: int = CANVAS_H
) -> tuple[int, int, int, int]:
    """Scaled size and offset ``(w, h, x, y)`` that fits the source inside the canvas, aspect kept."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source dimensions must be positive")
    scale = min(dst_w / src_w, dst_h / src_h)
    w = max(2, min(dst_w, round(src_w * scale)))
    h = max(2, min(dst_h, round(src_h * scale)))
    return w, h, (dst_w - w) // 2, (dst_h - h) // 2


def letterbox_image(image: np.ndarray, dst_w: int = CANVAS_W, dst_h: int = CANVAS_H) -> np.ndarray:
    """Letterbox a BGR ``ndarray`` onto a black canvas (reference implementation; also used in tests)."""
    import cv2

    h, w = image.shape[:2]
    sw, sh, x, y = letterbox_geometry(w, h, dst_w, dst_h)
    if (sw, sh) == (w, h) and (w, h) == (dst_w, dst_h):
        return np.ascontiguousarray(image)
    interpolation = cv2.INTER_AREA if sw < w else cv2.INTER_LINEAR
    canvas = np.zeros((dst_h, dst_w, 3), np.uint8)
    canvas[y : y + sh, x : x + sw] = cv2.resize(image, (sw, sh), interpolation=interpolation)
    return canvas


def frame_to_canvas(frame: av.VideoFrame, dst_w: int = CANVAS_W, dst_h: int = CANVAS_H) -> np.ndarray:
    """Decode a PyAV frame straight to the canvas: one swscale pass, then a paste when padded."""
    src_w, src_h = frame.width, frame.height
    sar = getattr(frame, "sample_aspect_ratio", None)  # absent in some PyAV builds
    if sar is not None and sar.numerator > 0 and sar.denominator > 0 and sar != 1:
        src_w = max(1, round(src_w * float(sar)))
    w, h, x, y = letterbox_geometry(src_w, src_h, dst_w, dst_h)
    if (w, h) == (dst_w, dst_h):
        return frame.to_ndarray(format="bgr24", width=dst_w, height=dst_h)
    scaled = frame.to_ndarray(format="bgr24", width=w, height=h)
    canvas = np.zeros((dst_h, dst_w, 3), np.uint8)
    canvas[y : y + h, x : x + w] = scaled
    return canvas


# --------------------------------------------------------------------------- queues and audio
class DropOldestQueue:
    """Bounded FIFO. A full queue evicts its oldest item and counts it; ``get`` blocks with timeout."""

    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self._items: deque[Any] = deque()
        self._cv = threading.Condition()
        self._closed = False
        self.dropped = 0
        self.high_water = 0

    def put(self, item: Any) -> int:
        """Enqueue; returns how many items were evicted (0 or 1)."""
        with self._cv:
            if self._closed:
                return 0
            evicted = 0
            if len(self._items) >= self.maxsize:
                self._items.popleft()
                self.dropped += 1
                evicted = 1
            self._items.append(item)
            self.high_water = max(self.high_water, len(self._items))
            self._cv.notify()
            return evicted

    def get(self, timeout: float) -> Any | None:
        with self._cv:
            if not self._items and not self._closed:
                self._cv.wait(timeout)
            return self._items.popleft() if self._items else None

    def __len__(self) -> int:
        with self._cv:
            return len(self._items)

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._items.clear()
            self._cv.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed


class AudioBlocker:
    """Cut resampled audio into fixed blocks tagged with the absolute sample index.

    The index is ``round((pts - origin) * rate)`` for the first sample and then counts samples, so
    it stays on the video PTS clock. A gap in the stream is filled with silence (up to ``max_gap_s``),
    an overlap is trimmed; neither shifts later blocks off the clock.
    """

    def __init__(self, rate: int = AUDIO_RATE, block: int = AUDIO_BLOCK_SAMPLES, max_gap_s: float = 2.0):
        self.rate, self.block = rate, block
        self.max_gap = int(max_gap_s * rate)
        self.tolerance = int(0.005 * rate)  # 5 ms of timestamp jitter is not a gap
        self._buf = np.zeros(0, np.int16)
        self._base: int | None = None  # absolute index of _buf[0]
        self.silence_inserted = 0
        self.samples_trimmed = 0

    def push(self, pts_rel: float, samples: np.ndarray) -> list[tuple[int, np.ndarray]]:
        """``pts_rel`` is the first sample's time in seconds relative to the generation origin."""
        start = round(pts_rel * self.rate)
        if start < 0:
            cut = min(-start, len(samples))
            self.samples_trimmed += cut
            samples, start = samples[cut:], start + cut
            if len(samples) == 0:
                return []
        if self._base is None:
            self._base = start
        else:
            expected = self._base + len(self._buf)
            gap = start - expected
            if gap > self.tolerance:
                fill = min(gap, self.max_gap)
                if gap > self.max_gap:  # beyond the cap: flush what we have and jump
                    pad = (-len(self._buf)) % self.block
                    self._buf = np.concatenate([self._buf, np.zeros(pad, np.int16)])
                    out = self._emit()
                    self._base = start
                    return out + self._append(samples)
                self._buf = np.concatenate([self._buf, np.zeros(fill, np.int16)])
                self.silence_inserted += fill
            elif gap < -self.tolerance:
                cut = min(-gap, len(samples))
                self.samples_trimmed += cut
                samples = samples[cut:]
                if len(samples) == 0:
                    return []
        return self._append(samples)

    def _append(self, samples: np.ndarray) -> list[tuple[int, np.ndarray]]:
        self._buf = np.concatenate([self._buf, samples.astype(np.int16, copy=False)])
        return self._emit()

    def _emit(self) -> list[tuple[int, np.ndarray]]:
        out: list[tuple[int, np.ndarray]] = []
        assert self._base is not None
        while len(self._buf) >= self.block:
            out.append((self._base, self._buf[: self.block].copy()))
            self._buf = self._buf[self.block :]
            self._base += self.block
        return out


# --------------------------------------------------------------------------- reader
def default_container_options(url: str) -> dict[str, str]:
    """Low-latency PyAV options per transport.

    The relay reads MediaMTX over RTMP, not RTSP: FFmpeg's RTSP demuxer re-bases packet timestamps
    when the first RTCP sender reports arrive (measured: a +5.9 s jump in both streams after about
    40 s, with audio and video moving 2 s apart), which would break the A/V clock. FLV timestamps
    are one clock per connection. ``flags=low_delay`` is not used with FLV: it stalls the read after a few frames.
    """
    scheme = url.split("://", 1)[0].lower()
    if scheme == "rtmp":
        return {"rtmp_live": "live", "probesize": "32768", "analyzeduration": "100000"}
    if scheme == "rtsp":
        return {
            "rtsp_transport": "tcp",
            "probesize": "32768",
            "analyzeduration": "100000",
            "fflags": "nobuffer",
            "flags": "low_delay",
        }
    return {}


class NetworkInput:
    """Reconnecting PyAV reader of ``url`` (the loopback RTSP/TCP path, or any PyAV-readable source).

    Implements ``FrameSource``. Decoding is on the CPU (PyAV); the GPU is not needed for 720p30.
    ``url`` may carry credentials: it lives in memory only and is dropped on ``close()``.
    """

    _RECONNECT_DELAYS = (0.2, 0.5, 1.0)

    def __init__(
        self,
        url: str,
        *,
        fps: float = 30.0,
        canvas: tuple[int, int] = (CANVAS_W, CANVAS_H),
        bus: Any | None = None,
        session_id: str = "relay",
        hold_after_s: float = 1.0,
        queue_size: int = 8,
        audio_queue_blocks: int = 150,
        open_timeout: float = 2.0,
        read_timeout: float = 5.0,  # longer than the longest keyframe interval a phone app uses
        container_options: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        start: bool = True,
    ):
        if tuple(canvas) != (CANVAS_W, CANVAS_H):
            raise ValueError("the canonical canvas is 1280x720")
        av.logging.set_level(None)  # libav messages can contain credentialed URLs
        self._url: str | None = url
        self.fps = float(fps)
        self._bus = bus
        self._session_id = session_id
        self._hold_after_s = hold_after_s
        self._clock = clock
        self._open_timeout, self._read_timeout = open_timeout, read_timeout
        self._options = dict(
            container_options if container_options is not None else default_container_options(url)
        )
        self._video = DropOldestQueue(queue_size)
        self._audio = DropOldestQueue(audio_queue_blocks)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._close_hooks: list[Callable[[], None]] = []
        self._closed = False

        self.generation = 0
        self._seq = 0
        self._last_media: float | None = None  # clock() of the most recent decoded frame
        self._prev_arrival: float | None = None
        self._streak = 0  # consecutive frames within the normal spacing
        self._hold_active = False
        self.stats_counts: Counter[str] = Counter()
        self.failure_counts: Counter[str] = Counter()
        self.error_code: str | None = None
        self.max_pts_gap_s = 0.0
        self.last_failure: dict[str, Any] | None = None
        self.drop_events: deque[dict[str, Any]] = deque(maxlen=64)

        self._thread = threading.Thread(target=self._run, name="m2-input", daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name="relay-ingest-watchdog", daemon=True)
        if start:
            self._thread.start()
            self._watchdog.start()

    def __repr__(self) -> str:  # never show the URL
        return f"NetworkInput(generation={self.generation}, fps={self.fps:g})"

    # --- FrameSource ---
    def frames(self) -> Iterator[Frame]:
        while True:
            item = self._video.get(0.1)
            if item is None:
                if self._video.closed:
                    return
                continue
            yield item

    def audio_blocks(self) -> Iterator[tuple[int, Any]]:
        while True:
            item = self._audio.get(0.1)
            if item is None:
                if self._audio.closed:
                    return
                continue
            yield item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        for thread in (self._thread, self._watchdog):
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5)
        self._video.close()
        self._audio.close()
        self._url = None
        for hook in self._close_hooks:
            try:
                hook()
            except Exception:
                log.debug("close hook failed", exc_info=True)
        self._close_hooks.clear()

    def add_close_hook(self, hook: Callable[[], None]) -> None:
        self._close_hooks.append(hook)

    # --- derived ---
    @property
    def samples_per_frame(self) -> float:
        """Audio samples per video frame at the stream's own rate (1600 at 30 fps)."""
        return AUDIO_RATE / self.fps

    @property
    def hold_active(self) -> bool:
        return self._hold_active

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(self.stats_counts)
        out.update(
            frames_dropped_queue=self._video.dropped,
            audio_blocks_dropped_queue=self._audio.dropped,
            video_queue_high_water=self._video.high_water,
            generation=self.generation,
            fps=self.fps,
            max_pts_gap_s=self.max_pts_gap_s,
            failures=dict(self.failure_counts),
        )
        return out

    # --- bus ---
    def _publish(self, topic: Topic, event: Any) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(topic, event)
        except Exception:
            log.debug("bus publish failed", exc_info=True)

    def _assert_hold(self) -> None:
        with self._lock:
            if self._hold_active:
                return
            self._hold_active = True
        self.stats_counts["hold_asserted"] += 1
        self._publish(Topic.HOLD, HoldCommand(HoldSource.AUTO_TRANSPORT, True, HOLD_ACTOR))
        self._publish(
            Topic.SAFETY_EVENT,
            SafetyEvent(
                event_id=uuid.uuid4().hex,
                session_id=self._session_id,
                wall_ts=datetime.now(UTC).isoformat(),
                pts=None,
                modality=Modality.SYSTEM,
                category=Category.HOLD,
                action="held",
                confidence=1.0,
                bbox=None,
                duration_ms=None,
                source="relay.ingest",
                detail_code="ingest_drop",
            ),
        )

    def _release_hold(self) -> None:
        with self._lock:
            if not self._hold_active:
                return
            self._hold_active = False
        self.stats_counts["hold_released"] += 1
        self._publish(Topic.HOLD, HoldCommand(HoldSource.AUTO_TRANSPORT, False, HOLD_ACTOR))

    def notify_transport_event(self, code: str) -> None:
        """Hard evidence that the phone link is gone: assert the transport hold at once.

        Fed by the MediaMTX supervisor (``publisher-disconnected`` on ``camera``, ``mediamtx-exit``).
        The frame-arrival watchdog stays the authority; this only makes a clean disconnect faster than
        ``hold_after_s``. It is safe against false alarms: when a new publisher replaces a stale one,
        the hold asserts and the first two in-spacing frames of the new connection release it.
        Before the first frame there is nothing to protect (the engine starts in PROTECT), so it is
        ignored until media has been seen.
        """
        if code not in TRANSPORT_LOSS_CODES or self._closed or self._last_media is None:
            return
        self.stats_counts["transport_events"] += 1
        self.stats_counts["transport_event_" + code.replace("-", "_")] += 1
        self._streak = 0  # recovery needs fresh frames, not the backlog already queued
        self._assert_hold()

    def _note_media(self, arrival: float) -> None:
        """Record one decoded frame for the transport hold.

        A frame that arrives more than ``hold_after_s`` after the previous one ends a stall, but it
        is not yet proof of recovery: when a stream dies, the demuxer releases its held last frame
        at end-of-stream, a second or more late. So a late frame asserts the hold itself and only
        a second frame in a row, within the normal spacing, releases it.
        """
        previous = self._prev_arrival
        if previous is not None and arrival - previous > self._hold_after_s:
            self._streak = 1
            self._assert_hold()
        else:
            self._streak += 1
        self._prev_arrival = arrival
        self._last_media = arrival
        if self._streak >= 2:
            self._release_hold()

    def _watch(self) -> None:
        while not self._stop.wait(0.05):
            last = self._last_media
            if last is not None and not self._hold_active and self._clock() - last > self._hold_after_s:
                self._assert_hold()

    # --- reader thread ---
    def _run(self) -> None:
        failures_in_row = 0
        while not self._stop.is_set():
            reason, error_class, got_media = "input-open", None, False
            self._gen_had_media = False
            try:
                got_media, reason = self._read_once()
            except Exception as error:  # retain only the fixed phase and the class name
                error_class = type(error).__name__
                reason = self._phase
                got_media = self._gen_had_media
            if self._stop.is_set():
                break
            self.failure_counts[reason] += 1
            now = self._clock()
            self.error_code = reason
            self.last_failure = {
                "code": reason,
                "error_class": error_class,
                "generation": self.generation,
                "at": now,
            }
            self.drop_events.append({"code": reason, "generation": self.generation, "at": now})
            failures_in_row = 0 if got_media else failures_in_row + 1
            delay = self._RECONNECT_DELAYS[min(failures_in_row, len(self._RECONNECT_DELAYS) - 1)]
            self._stop.wait(delay)

    _phase = "input-open"
    _gen_had_media = False

    def _read_once(self) -> tuple[bool, str]:
        self._phase = "input-open"
        url = self._url
        if url is None:
            return False, "input-closed"
        with av.open(
            url, timeout=(self._open_timeout, self._read_timeout), container_options=self._options
        ) as container:
            video = next((s for s in container.streams if s.type == "video"), None)
            audio = next((s for s in container.streams if s.type == "audio"), None)
            if video is None and audio is None:
                raise ValueError("no media streams")
            for stream in (video, audio):
                if stream is not None:
                    stream.codec_context.thread_count = 2
            self.generation += 1
            generation = self.generation
            # average_rate is the container's own claim (often absent over RTSP); guessed_rate can be
            # twice the real rate for H.264 field flags, so it is not used. The reader also measures
            # the rate from frame spacing (see `deltas` below).
            rate = video.average_rate if video is not None else None
            if rate is not None and 1.0 <= float(rate) <= 120.0:
                self.fps = float(rate)
            deltas: deque[float] = deque(maxlen=31)
            self.stats_counts["generations"] += 1
            origin: float | None = None  # container PTS of the first video frame (the clock zero)
            last_pts: float | None = None
            blocker = AudioBlocker()
            resampler = av.AudioResampler(format="s16", layout="mono", rate=AUDIO_RATE)
            pending_audio: deque[tuple[float, np.ndarray]] = deque()
            audio_pts_fallback = 0.0
            got_media = False
            streams = [s for s in (video, audio) if s is not None]
            self._phase = "input-decode"

            def emit_audio(pts_abs: float, samples: np.ndarray) -> None:
                nonlocal origin
                assert origin is not None
                for index, pcm in blocker.push(pts_abs - origin, samples):
                    self._audio.put(AudioBlock(index, pcm, generation))

            for packet in container.demux(*streams):
                if self._stop.is_set():
                    return got_media, "input-closed"
                if packet.size == 0:  # end-of-stream flush marker: nothing to decode
                    continue
                t_packet = self._clock()
                for frame in packet.decode():
                    if isinstance(frame, av.VideoFrame):
                        if frame.pts is None or frame.time_base is None:
                            self.stats_counts["pts_missing"] += 1
                            continue
                        pts_abs = float(frame.pts * frame.time_base)
                        if origin is None:
                            origin = pts_abs
                            while pending_audio:
                                emit_audio(*pending_audio.popleft())
                        rel = pts_abs - origin
                        if last_pts is not None and rel <= last_pts:
                            self.stats_counts["pts_nonmonotonic_dropped"] += 1
                            continue
                        image = frame_to_canvas(frame)
                        arrival = self._clock()
                        if last_pts is not None:
                            gap = rel - last_pts
                            self.max_pts_gap_s = max(self.max_pts_gap_s, gap)
                            if gap > 1.5 / self.fps:
                                self.stats_counts["pts_gaps"] += 1
                            deltas.append(gap)
                            if len(deltas) >= 30 and rate is None:
                                # mean spacing: RTMP timestamps are whole milliseconds, so single
                                # deltas jitter between 33 and 34 ms
                                measured = len(deltas) / sum(deltas)
                                if 5.0 <= measured <= 120.0:
                                    self.fps = round(measured, 2)
                        last_pts = rel
                        seq = self._seq
                        self._seq += 1
                        self._video.put(IngestFrame(seq, rel, arrival, image, generation))
                        self.stats_counts["frames"] += 1
                        got_media = self._gen_had_media = True
                        self._note_media(arrival)  # the hold watchdog follows video, not audio
                        self._publish(
                            Topic.TIMING,
                            StageTiming(STAGE_INGEST_DECODE, seq, rel, t_packet, max(arrival, t_packet)),
                        )
                    elif isinstance(frame, av.AudioFrame):
                        for sound in resampler.resample(frame):
                            samples = sound.to_ndarray().reshape(-1)
                            if sound.pts is not None and sound.time_base is not None:
                                pts_abs = float(sound.pts * sound.time_base)
                            else:
                                pts_abs = audio_pts_fallback
                            audio_pts_fallback = pts_abs + len(samples) / AUDIO_RATE
                            if video is None and origin is None:
                                origin = pts_abs  # audio-only stream: its own first sample is zero
                            if origin is None:
                                pending_audio.append((pts_abs, samples))
                                while sum(len(s) for _, s in pending_audio) > 3 * AUDIO_RATE:
                                    pending_audio.popleft()
                                continue
                            emit_audio(pts_abs, samples)
                            self.stats_counts["audio_blocks_in"] += 1
                            if video is None:  # audio-only source: audio drives the watchdog
                                got_media = self._gen_had_media = True
                                self._note_media(self._clock())
            self._phase = "input-eof"
            return got_media, "input-eof"
