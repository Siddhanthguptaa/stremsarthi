"""Newest-frame-wins lane workers.

Adapted from IRL Shield ``shield/lanes.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``vision/LICENSE.irl-shield``). What changed: lanes carry ``Detector`` plugins instead of one backend;
scheduling is a stride (FAST) or a rate (ACCURATE/SLOW) taken from ``Config.detect``; the output is a
``contracts.LaneResult`` for every run, with ``ok=False`` when any detector raises; no image resizing
(detectors downscale for themselves, so the lane stays model-agnostic).

A slow lane drops work, never source frames: ``offer`` replaces any frame still waiting. Detectors in
one lane run sequentially on the same frame. Exception text is never stored (it can contain OCR text),
only the exception type.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from contracts import (
    Category,
    CategoryConfig,
    DetectConfig,
    DetectContext,
    Detection,
    Detector,
    Frame,
    Lane,
    LaneResult,
    MaskRequest,
)

SLOW_LANE_HZ = 1.0
RESULT_QUEUE_MAX = 256

# (frame, lane, detections of this run) -> (mask requests, aux detections). Planner/tracker hook.
Assembler = Callable[
    [Frame, Lane, tuple[Detection, ...]], tuple[tuple[MaskRequest, ...], tuple[Detection, ...]]
]


def _no_assembly(
    frame: Frame, lane: Lane, detections: tuple[Detection, ...]
) -> tuple[tuple[MaskRequest, ...], tuple[Detection, ...]]:
    return (), ()


@dataclass(frozen=True, slots=True)
class LaneSchedule:
    """When a lane takes a frame. ``stride`` keeps frames with ``seq % stride == 0``;
    ``rate_hz`` is a minimum period between runs (``None`` = no limit)."""

    stride: int = 1
    rate_hz: float | None = None

    def __post_init__(self) -> None:
        if self.stride < 1:
            raise ValueError("LaneSchedule.stride must be >= 1")
        if self.rate_hz is not None and not self.rate_hz > 0:
            raise ValueError("LaneSchedule.rate_hz must be > 0")

    @classmethod
    def from_config(cls, lane: Lane, detect: DetectConfig) -> LaneSchedule:
        """FAST: every ``detect.stride``-th frame (10 Hz at 30 fps, stride 3; no extra rate limit, so
        jitter cannot skip a frame). ACCURATE: newest frame at most every ``1/detect.accurate_hz`` s.
        SLOW: at most 1 Hz."""
        if lane is Lane.FAST:
            return cls(stride=detect.stride)
        if lane is Lane.ACCURATE:
            return cls(rate_hz=detect.accurate_hz)
        return cls(rate_hz=SLOW_LANE_HZ)


class LaneWorker:
    """One worker thread running a lane's detectors on the newest offered frame."""

    def __init__(
        self,
        lane: Lane,
        detectors: Sequence[Detector],
        schedule: LaneSchedule,
        *,
        categories: Mapping[Category, CategoryConfig] | None = None,
        allowlist: Any = None,
        assembler: Assembler | None = None,
        deadline_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lane = lane
        self.detectors = tuple(detectors)
        self.schedule = schedule
        self._categories: Mapping[Category, CategoryConfig] = dict(categories or {})
        self._allowlist = allowlist
        self._assemble: Assembler = assembler or _no_assembly
        self._deadline_s = deadline_s
        self._clock = clock
        self._period = 0.0 if schedule.rate_hz is None else 1.0 / schedule.rate_hz
        self._cond = threading.Condition()
        self._latest: Frame | None = None
        self._results: deque[LaneResult] = deque(maxlen=RESULT_QUEUE_MAX)
        self._previous: tuple[Detection, ...] = ()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self.offered = self.skipped = self.dropped = self.completed = self.failed = 0
        self.last_error: dict[str, str] = {}  # detector id -> exception type name (never the message)
        self.service_seconds: deque[float] = deque(maxlen=4096)
        self.detector_seconds: dict[str, deque[float]] = {d.id: deque(maxlen=4096) for d in self.detectors}

    # ------------------------------------------------------------------ public API
    def start(self) -> None:
        with self._cond:
            if self._thread is not None:
                raise RuntimeError(f"lane {self.lane.value} already started")
            self._thread = threading.Thread(target=self._run, name=f"vision-{self.lane.value}", daemon=True)
        self._thread.start()

    def offer(self, frame: Frame) -> bool:
        """Non-blocking. Returns True if the frame is now waiting for the worker."""
        with self._cond:
            if self._stopping:
                raise RuntimeError(f"lane {self.lane.value} is closed")
            self.offered += 1
            if frame.seq % self.schedule.stride:
                self.skipped += 1
                return False
            if self._latest is not None:
                self.dropped += 1  # newest wins: the older waiting frame is never processed
            self._latest = frame
            self._cond.notify()
            return True

    def drain(self) -> list[LaneResult]:
        with self._cond:
            out = list(self._results)
            self._results.clear()
            return out

    def close(self, timeout: float = 35.0) -> None:
        with self._cond:
            self._stopping = True
            self._latest = None
            self._cond.notify_all()
            thread = self._thread
        errors: list[str] = []
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                errors.append(f"lane {self.lane.value} did not stop within {timeout}s")
        if not errors:  # never close detectors under a running worker
            for det in self.detectors:
                try:
                    det.close()
                except Exception as exc:  # noqa: BLE001 - keep closing the others
                    errors.append(f"{det.id}.close: {type(exc).__name__}")
        if errors:
            raise RuntimeError("; ".join(errors))

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "stride": self.schedule.stride,
            "rate_hz": self.schedule.rate_hz,
            "offered": self.offered,
            "skipped": self.skipped,
            "dropped": self.dropped,
            "completed": self.completed,
            "failed": self.failed,
            "service_seconds": list(self.service_seconds),
            "detector_seconds": {k: list(v) for k, v in self.detector_seconds.items()},
        }

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        next_allowed = 0.0
        while True:
            with self._cond:
                while not self._stopping and (self._latest is None or self._clock() < next_allowed):
                    delay = max(0.0, next_allowed - self._clock()) if self._latest is not None else None
                    self._cond.wait(delay)
                if self._stopping:
                    return
                frame, self._latest = self._latest, None
            assert frame is not None
            began = self._clock()
            next_allowed = began + self._period
            result = self._run_once(frame, began)
            self.service_seconds.append(self._clock() - began)
            with self._cond:
                self._results.append(result)

    def _run_once(self, frame: Frame, began: float) -> LaneResult:
        deadline = None if self._deadline_s is None else began + self._deadline_s
        ctx = DetectContext(
            lane=self.lane,
            categories=self._categories,
            stride=self.schedule.stride,
            previous=self._previous,
            deadline=deadline,
            allowlist=self._allowlist,
        )
        detections: list[Detection] = []
        for det in self.detectors:
            t0 = self._clock()
            try:
                detections.extend(det.detect(frame, ctx))
            except Exception as exc:  # noqa: BLE001 - any detector failure marks the run not ok
                return self._fail(frame, det.id, exc)
            finally:
                self.detector_seconds[det.id].append(self._clock() - t0)
        dets = tuple(detections)
        try:
            requests, aux = self._assemble(frame, self.lane, dets)
        except Exception as exc:  # noqa: BLE001 - a planner/tracker failure is a failed run too
            return self._fail(frame, "assembler", exc)
        self.completed += 1
        self._previous = dets
        return LaneResult(
            self.lane, frame.seq, frame.pts, self._clock(), ok=True, requests=requests, aux_detections=aux
        )

    def _fail(self, frame: Frame, source: str, exc: Exception) -> LaneResult:
        self.failed += 1
        self._previous = ()
        self.last_error = {source: type(exc).__name__}
        return LaneResult(self.lane, frame.seq, frame.pts, self._clock(), ok=False)
