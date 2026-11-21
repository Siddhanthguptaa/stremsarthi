"""Test doubles that let a ``Session`` run on the CPU with synthetic media (no network, no GPU).

``SyntheticSource`` is a ``contracts.FrameSource`` built on ``contracts.fakes.FakeFrameSource`` (a
SPECIMEN-style stand-in card, no personal data) plus 48 kHz audio with an optional beep and an
optical stamp at the same PTS (A/V sync checks). ``FakeLaneHost`` is a ``contracts.LaneHost`` driven
by a ``contracts.fakes.FakeDetector``. ``RecordingSink`` is a ``contracts.FrameSink`` that keeps what
it was given.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from contracts import (
    AudioChunk,
    Category,
    Config,
    DetectContext,
    Detection,
    Frame,
    Lane,
    LaneResult,
    MaskKind,
    MaskRequest,
    MaskStyle,
    Quad,
    RelayEndpoints,
    StageTiming,
    Topic,
)
from contracts.fakes import FakeDetector, FakeFrameSource

from .audio_mute import RATE

STAMP = (slice(0, 64), slice(0, 64))  # optical stamp: a white square in the top-left corner
BEEP_HZ = 1000.0
BEEP_AMPLITUDE = 8000
BLOCK = RATE // 50  # 20 ms


class SyntheticSource:
    """``FrameSource`` of ``n_frames`` 1280x720 frames at ``fps`` with a stand-in card at ``card_quad``.

    Frame ``i`` has ``pts = i / fps`` and ``arrival = start + pts`` (virtual time unless ``realtime``,
    which sleeps until the arrival time so a threaded session sees a live feed). From ``stamp_at``
    seconds the corner square is white; from ``beep_at`` a 100 ms 1 kHz beep is in the audio.
    """

    def __init__(
        self,
        n_frames: int = 90,
        fps: int = 30,
        *,
        start: float = 10.0,
        card_quad: Quad | None = None,
        stamp_at: float | None = None,
        beep_at: float | None = None,
        realtime: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.n_frames, self.fps, self.start = n_frames, fps, start
        self.card = FakeFrameSource(n_frames, fps, card_quad, start_monotonic=start)
        self.card_quad = self.card.card_quad
        self.stamp_at, self.beep_at = stamp_at, beep_at
        self.realtime, self._clock = realtime, clock
        self.closed = threading.Event()

    def _wait_until(self, t: float) -> bool:
        while self._clock() < t:
            if self.closed.wait(min(0.005, max(0.0, t - self._clock()))):
                return False
        return not self.closed.is_set()

    def frames(self) -> Iterator[Frame]:
        for frame in self.card:
            if self.realtime and not self._wait_until(frame.arrival):
                return
            if self.closed.is_set():
                return
            image = frame.image
            if self.stamp_at is not None and frame.pts >= self.stamp_at - 1e-9:
                image = np.array(image)
                image[STAMP] = 255
                image.setflags(write=False)
            yield Frame(frame.seq, frame.pts, frame.arrival, image)

    def audio_blocks(self) -> Iterator[tuple[int, Any]]:
        total = int(self.n_frames / self.fps * RATE)
        for first in range(0, total, BLOCK):
            if self.realtime and not self._wait_until(self.start + (first + BLOCK) / RATE):
                return
            idx = np.arange(first, first + BLOCK)
            pcm = np.zeros(BLOCK, np.int16)
            if self.beep_at is not None:
                b0 = round(self.beep_at * RATE)
                inside = (idx >= b0) & (idx < b0 + RATE // 10)
                tone = BEEP_AMPLITUDE * np.sin(2 * np.pi * BEEP_HZ * idx / RATE)
                pcm = np.where(inside, tone, 0).astype(np.int16)
            yield first, pcm

    def close(self) -> None:
        self.closed.set()


@dataclass(slots=True)
class SinkRecord:
    pts: float
    audio: np.ndarray
    written_at: float
    probe: Any
    image: np.ndarray | None


class RecordingSink:
    """``FrameSink`` that records every write. ``probe(image)`` runs at write time (cheap checks only);
    ``keep_images`` retains the arrays (about 2.7 MB each)."""

    def __init__(
        self,
        probe: Callable[[np.ndarray], Any] | None = None,
        *,
        keep_images: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.probe, self.keep_images, self._clock = probe, keep_images, clock
        self.records: list[SinkRecord] = []
        self.healthy = True
        self.closed = False
        self._lock = threading.Lock()

    def write(self, pts: float, image: Any, audio_pcm48k: Any) -> None:
        record = SinkRecord(
            pts,
            np.array(audio_pcm48k),
            self._clock(),
            self.probe(image) if self.probe else None,
            np.array(image) if self.keep_images else None,
        )
        with self._lock:
            self.records.append(record)

    def close(self) -> None:
        self.closed = True


class FakeLaneHost:
    """``LaneHost`` that runs a ``FakeDetector`` on every ``stride``-th frame (virtual time).

    A result for frame ``s`` completes at ``frame.arrival + latency_s``. Every run is reported,
    including empty ones (the ``ok=True`` heartbeat the fail-safe needs). ``kill()`` makes the host
    go silent (a dead detector); ``fail_from(seq)`` reports ``ok=False`` from that frame on.
    """

    def __init__(
        self,
        detector: FakeDetector | None = None,
        *,
        lane: Lane = Lane.FAST,
        stride: int = 3,
        latency_s: float = 0.05,
        pad_px: float = 12.0,
        style: MaskStyle = MaskStyle.BLUR,
        valid_for_s: float = 0.2,
        track_id: int | None = None,
    ) -> None:
        self.detector = detector or FakeDetector()
        self.lane, self.stride, self.latency_s = lane, stride, latency_s
        self.pad_px, self.style, self.valid_for_s, self.track_id = pad_px, style, valid_for_s, track_id
        self.active_lanes = frozenset({lane})
        self.started = self.closed = False
        self.killed = False
        self.fail_from_seq: int | None = None
        self.submitted: list[int] = []
        self._pending: deque[LaneResult] = deque()

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def kill(self) -> None:
        self.killed = True

    def fail_from(self, seq: int) -> None:
        self.fail_from_seq = seq

    def submit(self, frame: Frame) -> None:
        self.submitted.append(frame.seq)
        if self.killed or frame.seq % self.stride:
            return
        done = frame.arrival + self.latency_s
        if self.fail_from_seq is not None and frame.seq >= self.fail_from_seq:
            self._pending.append(LaneResult(self.lane, frame.seq, frame.pts, done, ok=False))
            return
        ctx = DetectContext(lane=self.lane, categories={}, stride=self.stride)
        requests: list[MaskRequest] = []
        aux: list[Detection] = []
        for k, det in enumerate(self.detector.detect(frame, ctx)):
            if det.allow:  # an allowlisted face is never masked; vision hands it over for its mouth quad
                aux.append(det)
                continue
            quad = det.quad.padded(self.pad_px).clamped()
            requests.append(
                MaskRequest(
                    request_id=f"{det.detector}-{frame.seq}-{k}",
                    source=det.detector,
                    category=det.category,
                    kind=MaskKind.REGION,
                    quads=(quad,),
                    frame_pts=frame.pts,
                    pts_start=frame.pts,
                    pts_end=frame.pts + self.valid_for_s,
                    style=self.style,
                    confidence=det.confidence,
                    reason="fake",
                    completed_at=done,
                    track_id=det.track_id if det.track_id is not None else self.track_id,
                )
            )
        self._pending.append(
            LaneResult(
                self.lane,
                frame.seq,
                frame.pts,
                done,
                ok=True,
                requests=tuple(requests),
                aux_detections=tuple(aux),
            )  # fmt: skip
        )

    def poll(self) -> list[LaneResult]:
        out = list(self._pending)
        self._pending.clear()
        return out


def card_detector(n_frames: int, quad: Quad, category: Category = Category.AADHAAR) -> FakeDetector:
    """A ``FakeDetector`` that reports ``quad`` on every frame."""
    from contracts import Detection

    script = {
        seq: [Detection("fake", category, quad, 0.95, seq, seq / 30.0, 0.0, track_id=None)]
        for seq in range(n_frames)
    }
    return FakeDetector(script=script)


class FakeAudioHost:
    """``AudioHost`` that records the 16 kHz chunks it is fed."""

    def __init__(self) -> None:
        self.started = self.closed = False
        self.chunks: list[AudioChunk] = []

    def start(self) -> None:
        self.started = True

    def feed(self, chunk: AudioChunk) -> None:
        self.chunks.append(chunk)

    def close(self) -> None:
        self.closed = True


# ``module:build`` entry points so ``engine.main`` can run end to end on synthetic media:
#   python -m engine.main --module relay=engine.session_fakes:build_relay #       --module vision=engine.session_fakes:build_vision --module audio=engine.session_fakes:build_audio
FAKE_FRAMES = 150


def build_relay(config: Config, bus: Any) -> RelayEndpoints:
    source = SyntheticSource(
        FAKE_FRAMES, config.fps, start=time.monotonic() + 0.05, stamp_at=1.0, beep_at=1.0, realtime=True
    )
    return RelayEndpoints(source=source, sink=RecordingSink())


def build_vision(config: Config, bus: Any) -> FakeLaneHost:
    return FakeLaneHost(card_detector(FAKE_FRAMES, SyntheticSource(1, config.fps).card_quad))


class HeartbeatAudioHost(FakeAudioHost):
    """``FakeAudioHost`` that publishes ``audio_heartbeat`` timings once a second, like the real host."""

    def __init__(self, bus: Any, period_s: float = 1.0) -> None:
        super().__init__()
        self._bus, self._period = bus, period_s
        self._last_pts: float | None = None  # newest chunk pts_end seen, like the real host's beat
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def feed(self, chunk: AudioChunk) -> None:
        super().feed(chunk)
        self._last_pts = float(chunk.pts_end)

    def start(self) -> None:
        super().start()
        self._thread = threading.Thread(target=self._beat, name="fake-audio-heartbeat", daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        seq = 0
        while not self._stop.is_set():
            now = time.monotonic()
            self._bus.publish(Topic.TIMING, StageTiming("audio_heartbeat", seq, self._last_pts, now, now))
            seq += 1
            self._stop.wait(self._period)

    def close(self) -> None:
        self._stop.set()
        super().close()


def build_audio(config: Config, bus: Any) -> FakeAudioHost:
    return HeartbeatAudioHost(bus)
