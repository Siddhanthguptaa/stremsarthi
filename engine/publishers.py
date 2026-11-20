"""Engine-side publishers: ``SafetyEvent`` (masked, held, failsafe) and ``EngineStatus`` at 1 Hz.

New code (docs/specs/engine.md 4 and 5.4). Privacy: events carry category, time, confidence and a
bounding box only, never pixels or text.

``SafetyPublisher`` publishes one ``masked`` event when a track first appears in an emitted frame
(never one per frame) and publishes the same ``event_id`` again with ``duration_ms`` filled in when
the track has been absent for ``track_gap_s``: a consumer upserts by ``event_id``. A track is a
``(category, track_id)`` pair; requests with no ``track_id`` are associated by bounding-box IoU.
``failsafe`` works the same way for each PROTECT episode (warm-up before the first LIVE frame is not
reported), and ``held`` is published when a hold source is released, with the time it was held.
The relay already reports its own ``auto_transport`` hold, so that source is skipped by default.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from contracts import (
    CONTRACT_VERSION,
    Category,
    EngineStatus,
    HoldSource,
    Modality,
    MuteSpan,
    Quad,
    SafetyEvent,
    Topic,
)

from .deadline import DeadlineEngine, ProjectedMask


def _wall_ts() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class _Track:
    event_id: str
    key: tuple[Any, ...]
    category: Category
    start_pts: float
    last_pts: float
    quad: Quad
    source: str
    confidence: float
    reason: str
    tracked: bool


class SafetyPublisher:
    def __init__(
        self,
        bus: Any,
        session_id: str,
        *,
        fps: float = 30.0,
        track_gap_s: float = 0.5,
        iou_match: float = 0.3,
        skip_hold_sources: Iterable[HoldSource] = (HoldSource.AUTO_TRANSPORT,),
        wall: Callable[[], str] = _wall_ts,
    ) -> None:
        self.bus = bus
        self.session_id = session_id
        self.fps = float(fps)
        self.track_gap_s = track_gap_s
        self.iou_match = iou_match
        self.skip_hold_sources = frozenset(skip_hold_sources)
        self._wall = wall
        self._tracks: dict[tuple[Any, ...], _Track] = {}
        self._failsafe: tuple[str, float, str, float] | None = None  # event_id, start pts, reason, last pts
        self._warm = False
        self._holds: dict[HoldSource, tuple[float, float | None]] = {}  # source -> (monotonic since, pts)
        self.events = 0

    # ------------------------------------------------------------------ masked

    def frame(self, pts: float, masks: Iterable[ProjectedMask]) -> None:
        """Account the masks painted on one emitted LIVE frame; closes tracks that went quiet."""
        for projected in masks:
            request = projected.request
            for index, quad in enumerate(projected.quads):
                track = self._match(request.category, request.track_id, index, quad)
                if track is None:
                    self._start(request, index, quad, pts)
                else:
                    track.last_pts = pts
                    track.quad = quad
        self._expire(pts)

    def _match(self, category: Category, track_id: int | None, index: int, quad: Quad) -> _Track | None:
        if track_id is not None:
            return self._tracks.get((category, track_id, index))
        best: _Track | None = None
        best_iou = self.iou_match
        for track in self._tracks.values():
            if track.tracked or track.category is not category:
                continue
            iou = track.quad.iou(quad)
            if iou >= best_iou:
                best, best_iou = track, iou
        return best

    def _start(self, request: Any, index: int, quad: Quad, pts: float) -> None:
        tracked = request.track_id is not None
        key: tuple[Any, ...] = (
            (request.category, request.track_id, index)
            if tracked
            else (request.category, "u", uuid.uuid4().hex)
        )
        track = _Track(
            uuid.uuid4().hex, key, request.category, pts, pts, quad, request.source, request.confidence,
            request.reason, tracked,
        )  # fmt: skip
        self._tracks[key] = track
        self._publish_masked(track, None)

    def _expire(self, pts: float) -> None:
        for key, track in list(self._tracks.items()):
            if pts - track.last_pts > self.track_gap_s:
                self._publish_masked(track, self._duration_ms(track.start_pts, track.last_pts))
                del self._tracks[key]

    def _duration_ms(self, start: float, last: float) -> int:
        return max(0, round((last - start + 1.0 / self.fps) * 1000.0))

    def _publish_masked(self, track: _Track, duration_ms: int | None) -> None:
        self._publish(
            SafetyEvent(
                event_id=track.event_id,
                session_id=self.session_id,
                wall_ts=self._wall(),
                pts=track.start_pts,
                modality=Modality.VIDEO,
                category=track.category,
                action="masked",
                confidence=track.confidence,
                bbox=track.quad,
                duration_ms=duration_ms,
                source=track.source,
                detail_code=track.reason,
            )
        )

    # ------------------------------------------------------------------ failsafe

    def state(self, state: str, reason: str, pts: float) -> None:
        """Feed every emitted frame's final state; one failsafe event per PROTECT episode."""
        if state == "LIVE":
            self._warm = True
        if state == "PROTECT" and self._warm:
            if self._failsafe is None:
                self._failsafe = (uuid.uuid4().hex, pts, reason, pts)
                self._publish_failsafe(self._failsafe, None)
            else:
                event_id, start, first_reason, _ = self._failsafe
                self._failsafe = (event_id, start, first_reason, pts)
        elif self._failsafe is not None and state != "PROTECT":
            self._close_failsafe()

    def _close_failsafe(self) -> None:
        if self._failsafe is not None:
            event_id, start, reason, last = self._failsafe
            self._publish_failsafe(self._failsafe, self._duration_ms(start, last))
            self._failsafe = None

    def _publish_failsafe(self, record: tuple[str, float, str, float], duration_ms: int | None) -> None:
        event_id, start, reason, _ = record
        self._publish(
            SafetyEvent(
                event_id=event_id,
                session_id=self.session_id,
                wall_ts=self._wall(),
                pts=start,
                modality=Modality.SYSTEM,
                category=Category.FAILSAFE,
                action="failsafe",
                confidence=1.0,
                bbox=None,
                duration_ms=duration_ms,
                source="engine",
                detail_code=reason,
            )
        )

    # ------------------------------------------------------------------ muted

    def mute(self, span: MuteSpan, *, late: bool = False, partial_ms: int = 0) -> None:
        """One event per ``span_id`` (a revised span republishes the same ``event_id``: consumers upsert).

        A ``failsafe`` span (unverified speech muted by the audio module's deadline) is ``failsafe``,
        any other is ``muted``; both carry the span's own category and reason code and its duration.
        A span that arrived after its audio left is a system ``warned`` event coded ``late_span``
        (``partial_ms`` is the part that had already left, in the code only: never text or audio).
        """
        if late:
            code = "late_span" if partial_ms <= 0 else f"late_span_partial_{partial_ms}ms"
            self._publish(
                SafetyEvent(
                    event_id=f"late-{span.span_id}",
                    session_id=self.session_id,
                    wall_ts=self._wall(),
                    pts=span.pts_start,
                    modality=Modality.AUDIO,
                    category=Category.FAILSAFE,
                    action="warned",
                    confidence=1.0,
                    bbox=None,
                    duration_ms=max(0, round((span.pts_end - span.pts_start) * 1000.0)),
                    source="engine",
                    detail_code=code,
                )
            )
            return
        self._publish(
            SafetyEvent(
                event_id=f"mute-{span.span_id}",
                session_id=self.session_id,
                wall_ts=self._wall(),
                pts=span.pts_start,
                modality=Modality.AUDIO,
                category=span.category,
                action="failsafe" if span.granularity == "failsafe" else "muted",
                confidence=span.confidence,
                bbox=None,
                duration_ms=max(0, round((span.pts_end - span.pts_start) * 1000.0)),
                source="audio",
                detail_code=span.reason,
            )
        )

    # ------------------------------------------------------------------ held

    def hold(self, source: HoldSource, active: bool, now: float, pts: float | None) -> None:
        """Call on every hold command; publishes ``held`` (with duration) when a source is released."""
        if active:
            self._holds.setdefault(source, (now, pts))
            return
        record = self._holds.pop(source, None)
        if record is None or source in self.skip_hold_sources:
            return
        since, start_pts = record
        self._publish(
            SafetyEvent(
                event_id=uuid.uuid4().hex,
                session_id=self.session_id,
                wall_ts=self._wall(),
                pts=start_pts,
                modality=Modality.SYSTEM,
                category=Category.HOLD,
                action="held",
                confidence=1.0,
                bbox=None,
                duration_ms=max(0, round((now - since) * 1000.0)),
                source=source.value,
                detail_code="hold",
            )
        )

    # ------------------------------------------------------------------ end

    def close(self, now: float | None = None) -> None:
        """Flush open tracks, the failsafe episode and holds still active (session end)."""
        for track in list(self._tracks.values()):
            self._publish_masked(track, self._duration_ms(track.start_pts, track.last_pts))
        self._tracks.clear()
        self._close_failsafe()
        now = time.monotonic() if now is None else now
        for source in list(self._holds):
            self.hold(source, False, now, None)

    def _publish(self, event: SafetyEvent) -> None:
        self.events += 1
        self.bus.publish(Topic.SAFETY_EVENT, event)


class StatusPublisher:
    """``EngineStatus`` once per ``interval_s`` seconds of the engine clock."""

    State = Literal["starting", "ready", "waiting_ingest", "live", "stopping", "error"]

    def __init__(
        self,
        bus: Any,
        engine: DeadlineEngine,
        *,
        interval_s: float = 1.0,
        window_s: float = 5.0,
        process_clock: Callable[[], float] = time.process_time,
    ) -> None:
        self.bus = bus
        self.engine = engine
        self.interval_s = interval_s
        self.window_s = window_s
        self._process_clock = process_clock
        self._emits: deque[float] = deque()
        self._delays: deque[tuple[float, float]] = deque()
        self._last: float | None = None
        self._cpu_last: tuple[float, float] | None = None
        self._protect_age = 0.0
        self.allowlist_version = 0
        self.published = 0

    def observe(self, now: float, arrival: float | None, protect_age: float) -> None:
        """One frame left the engine at ``now``; ``arrival`` is its capture-side arrival time."""
        self._emits.append(now)
        if arrival is not None:
            self._delays.append((now, now - arrival))
        self._protect_age = protect_age
        horizon = now - self.window_s
        while self._emits and self._emits[0] < horizon:
            self._emits.popleft()
        while self._delays and self._delays[0][0] < horizon:
            self._delays.popleft()

    def snapshot(self, now: float, state: str) -> EngineStatus:
        control = self.engine.control.snapshot()
        recent = [t for t in self._emits if t >= now - 1.0]
        delays = [d for _, d in self._delays]
        cpu_now = (now, self._process_clock())
        cores: float | None = None
        if self._cpu_last is not None and cpu_now[0] > self._cpu_last[0]:
            cores = max(0.0, (cpu_now[1] - self._cpu_last[1]) / (cpu_now[0] - self._cpu_last[0]))
        self._cpu_last = cpu_now
        return EngineStatus(
            state=state,  # type: ignore[arg-type]
            hold_active=control.hold_active,
            hold_sources=control.hold_sources,
            delay_s_p50=round(statistics.median(delays), 3) if delays else None,
            fps=float(len(recent)) if self._emits else None,
            cpu_cores=None if cores is None else round(cores, 2),
            vram_mb=None,  # the engine is CPU-only
            allowlist_version=self.allowlist_version,
            lanes_stale=self.engine.lanes_stale(),
            protect_seconds=round(self._protect_age, 2),
            contract_version=CONTRACT_VERSION,
        )

    def tick(self, now: float, state: str) -> EngineStatus | None:
        if self._last is not None and now - self._last < self.interval_s:
            return None
        self._last = now
        status = self.snapshot(now, state)
        self.published += 1
        self.bus.publish(Topic.ENGINE_STATUS, status)
        return status
