"""Bus wiring for the risk part of the strike guard (T5.2).

Subscribes to transcript, detection and risk topics; publishes profanity and risky-text signals,
the 2 Hz ``RiskMeter`` and ``SafetyEvent(action="warned")``. The synchronous ``handle`` and
``tick`` are the testable core; ``run`` is a thin asyncio loop around them.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from contracts.config import Config, GuardConfig
from contracts.enums import Topic
from contracts.events import RiskSignal, StageTiming
from contracts.protocols import EventBus

from .aggregator import RiskAggregator, Update
from .profanity import ProfanityDetector
from .sources import SignalSources

log = logging.getLogger("guard.risk")
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def stream_wall_ts(t: float | None) -> str:
    """Deterministic wall_ts string derived from stream time (real wall time is not needed)."""
    return (_EPOCH + timedelta(seconds=t or 0.0)).isoformat()


class StreamClock:
    """Stream time: the newest PTS seen, extrapolated with a monotonic clock between events."""

    def __init__(self, mono: Callable[[], float] = time.monotonic) -> None:
        self._mono = mono
        self._pts = 0.0
        self._at = mono()
        self._seen = False

    def observe(self, pts: float | None) -> None:
        if pts is not None and (not self._seen or pts >= self.now()):
            self._pts, self._at, self._seen = pts, self._mono(), True

    def now(self) -> float:
        return self._pts + (self._mono() - self._at)


class RiskService:
    def __init__(
        self,
        cfg: GuardConfig,
        bus: EventBus | None = None,
        *,
        clock: StreamClock | None = None,
        wall_ts_fn: Callable[[float | None], str] = stream_wall_ts,
        session_id: str = "session",
    ) -> None:
        self.cfg = cfg
        self.bus = bus
        self.clock = clock or StreamClock()
        self.sources = SignalSources(
            ProfanityDetector(enabled=cfg.profanity_enabled, wall_ts_fn=wall_ts_fn), wall_ts_fn
        )
        self.agg = RiskAggregator(cfg, wall_ts_fn=lambda t: wall_ts_fn(t), session_id=session_id)
        self._own: list[RiskSignal] = []
        self._seq = 0

    def _emit(self, update: Update, out: list[tuple[Topic, object]]) -> None:
        out.append((Topic.RISK_METER, update.meter))
        out.extend((Topic.SAFETY_EVENT, w) for w in update.warnings)

    def handle(self, topic: Topic, event: object, now: float | None = None) -> list[tuple[Topic, object]]:
        """Process one bus event. Returns the events to publish (also published when a bus is set)."""
        if not self.cfg.enabled:
            return []
        t_start = time.monotonic()
        if isinstance(event, RiskSignal) and event in self._own:
            self._own.remove(event)  # our own publication coming back from the bus
            return []
        pts = getattr(event, "pts", None)
        if pts is None:
            pts = getattr(event, "pts_end", None)
        self.clock.observe(pts)
        t = self.clock.now() if now is None else now
        sigs = self.sources.from_event(topic, event)
        out: list[tuple[Topic, object]] = []
        if not sigs:
            return out
        for s in sigs:
            if topic is not Topic.RISK:
                out.append((Topic.RISK, s))
                self._own.append(s)
        del self._own[:-256]
        self._emit(self.agg.add_many(sigs, t), out)
        self._seq += 1
        if topic is Topic.TRANSCRIPT:
            out.append((Topic.TIMING, StageTiming("guard_risk", self._seq, pts, t_start, time.monotonic())))
        self._publish(out)
        return out

    def tick(self, now: float | None = None) -> list[tuple[Topic, object]]:
        if not self.cfg.enabled:
            return []
        upd = self.agg.tick(self.clock.now() if now is None else now)
        out: list[tuple[Topic, object]] = []
        if upd is not None:
            self._emit(upd, out)
            self._publish(out)
        return out

    def _publish(self, out: list[tuple[Topic, object]]) -> None:
        if self.bus is not None:
            for topic, ev in out:
                self.bus.publish(topic, ev)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        if self.bus is None:
            raise RuntimeError("RiskService.run needs a bus")
        sub = self.bus.subscribe([Topic.TRANSCRIPT, Topic.DETECTION, Topic.RISK])
        period = min(0.1, 1.0 / (4 * self.cfg.meter_hz))

        async def ticker() -> None:
            while stop is None or not stop.is_set():
                self.tick()
                await asyncio.sleep(period)

        task = asyncio.create_task(ticker())
        try:
            async for topic, ev in sub:
                self.handle(topic, ev)
                if stop is not None and stop.is_set():
                    break
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def build(config: Config, bus: EventBus) -> RiskService:
    return RiskService(config.guard, bus)
