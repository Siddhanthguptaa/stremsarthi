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

import json
import os
import re
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

ENV_FAULT = "SS_FAULT"
ENV_FAULT_LOG = "SS_FAULT_LOG"
_FAULT_TIME_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s)?$")


def _fault_seconds(text: str) -> float:
    m = _FAULT_TIME_RE.match(text.strip())
    if not m:
        raise ValueError(text)
    value = float(m.group(1))
    return value / 1000.0 if m.group(2) == "ms" else value


@dataclass(frozen=True, slots=True)
class _LaneFault:
    mode: str  # "hang" | "crash"
    at_s: float
    for_s: float | None

    def canonical(self, lane: str) -> str:
        tail = f"t={self.at_s:g}s" + (f",for={self.for_s:g}s" if self.for_s is not None else "")
        return f"lane:{lane}:{self.mode}@{tail}"


def _parse_lane_faults(value: str | None, lane: str) -> list[_LaneFault]:
    """Lane specs of an ``SS_FAULT`` value (``lane:<name>:<hang|crash>@t=<time>[,for=<time>]``, ``;``
    separated; protocol in ``eval/fault.py``) that target ``lane``. Re-implemented here on purpose:
    vision never imports ``eval``. Sink specs and malformed specs are ignored (the hook stays dormant)."""
    out: list[_LaneFault] = []
    for part in (value or "").split(";"):
        head, _, params = part.strip().partition("@")
        fields = head.split(":")
        if len(fields) != 3 or fields[0] != "lane" or fields[1] != lane or fields[2] not in ("hang", "crash"):
            continue
        at: float | None = None
        dur: float | None = None
        try:
            for item in params.split(","):
                key, eq, val = item.partition("=")
                if not eq:
                    raise ValueError(item)
                if key.strip() == "t":
                    at = _fault_seconds(val)
                elif key.strip() == "for":
                    dur = _fault_seconds(val)
                    if dur <= 0:
                        raise ValueError(item)
                else:
                    raise ValueError(item)
        except ValueError:
            continue
        if at is not None:
            out.append(_LaneFault(fields[2], at, dur))
    return out


class _FaultHook:
    """Per-lane SS_FAULT hook. Only built when a spec targets this lane. ``t = 0`` is the arrival of
    the first frame offered to the lane; activation/clearing is appended once to ``SS_FAULT_LOG`` as one
    JSON line in the same format as ``eval.fault.FaultInjector``."""

    def __init__(self, lane: str, faults: list[_LaneFault], log_path: str | None) -> None:
        self.lane = lane
        self.faults = faults
        self.log_path = log_path
        self._t0: float | None = None
        self._announced: set[tuple[str, str]] = set()

    def start(self, first_frame_arrival: float) -> None:
        if self._t0 is None:
            self._t0 = first_frame_arrival

    def check(self) -> _LaneFault | None:
        """The fault active now (hang wins over crash), or None."""
        if self._t0 is None:
            return None
        t = time.monotonic() - self._t0
        found: _LaneFault | None = None
        for f in self.faults:
            if t < f.at_s:
                continue
            if f.for_s is None or t < f.at_s + f.for_s:
                self._announce("fault_active", f)
                if found is None or f.mode == "hang":
                    found = f
            else:
                self._announce("fault_cleared", f)
        return found

    def hang(self, fault: _LaneFault) -> None:
        """Block the calling (daemon) worker thread: forever, or until the ``for=`` window ends."""
        if fault.for_s is None or self._t0 is None:
            threading.Event().wait()
            return
        remaining = self._t0 + fault.at_s + fault.for_s - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        self._announce("fault_cleared", fault)

    def _announce(self, event: str, fault: _LaneFault) -> None:
        spec = fault.canonical(self.lane)
        if (event, spec) in self._announced:
            return
        self._announced.add((event, spec))
        if not self.log_path:
            return
        end = fault.at_s if event == "fault_active" else (fault.at_s + (fault.for_s or 0.0))
        line = json.dumps(
            {
                "event": event,
                "spec": spec,
                "t_monotonic": round(end + (self._t0 or 0.0), 6),
                "t_observed": round(time.monotonic(), 6),
                "proc": "vision",
            },
            separators=(",", ":"),
        )
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
        except OSError:
            pass


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
        raw_fault = os.environ.get(ENV_FAULT)
        lane_faults = _parse_lane_faults(raw_fault, lane.value) if raw_fault else []
        # None unless SS_FAULT targets this lane: the production path pays one `is not None` test per run
        self._fault: _FaultHook | None = (
            _FaultHook(lane.value, lane_faults, os.environ.get(ENV_FAULT_LOG) or None)
            if lane_faults
            else None
        )
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
            if self._fault is not None:
                self._fault.start(frame.arrival)  # fault clock t=0: first frame offered
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
            crash = False
            if self._fault is not None:
                active = self._fault.check()
                if active is not None and active.mode == "hang":
                    self._fault.hang(active)  # blocks before the detector; no LaneResult is produced
                    if self._stopping:
                        return
                elif active is not None:
                    crash = True
            began = self._clock()
            next_allowed = began + self._period
            result = (
                self._fail(frame, "fault", RuntimeError("SS_FAULT lane crash"))
                if crash
                else self._run_once(frame, began)
            )
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
