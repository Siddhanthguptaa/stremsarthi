"""Live strike-guard risk state for ``GET /api/risk`` (the WebSocket carries ``guard.risk`` and
``guard.meter`` frames as they arrive; this is the REST snapshot for a page load or a poll).

Built from ``guard.meter`` (the guard's own aggregated level and score, with hysteresis) and
``guard.risk`` (the individual signals, each of which decays to zero after its ``ttl_s``). Nothing but
category, source, score and the fixed ``label`` code is kept; a signal's label is a code such as
``fp:demo_03``, never a lyric or a title.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from contracts import RiskMeter, RiskSignal, Topic

from .bus import BusClient, Subscription
from .events import parse_ts

log = logging.getLogger("streamsarthi.api.risk")

MAX_SIGNALS = 64
METER_STALE_S = 10.0  # the guard republishes about once a second; older means the guard is gone
LEVELS = ("green", "yellow", "orange", "red")
LEVEL_FLOORS = (0.0, 0.25, 0.50, 0.75)  # same bounds as guard.risk.meter.LOWER_BOUNDS


# The REST/web vocabulary (spec 5.2) for the contract's RiskLevel (guard.meter).
API_LEVEL = {"green": "ok", "yellow": "watch", "orange": "warn", "red": "danger"}


def level_for(score: float) -> str:
    idx = 0
    for i, floor in enumerate(LEVEL_FLOORS):
        if score >= floor:
            idx = i
    return LEVELS[idx]


@dataclass(slots=True)
class _Sig:
    signal: RiskSignal
    expires_mono: float


class RiskState:
    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._signals: deque[_Sig] = deque(maxlen=MAX_SIGNALS)
        self._meter: RiskMeter | None = None
        self._meter_mono = 0.0

    def observe(self, topic: Topic, event: Any) -> None:
        now = self._clock()
        if topic is Topic.RISK and isinstance(event, RiskSignal):
            self._signals.append(_Sig(event, now + event.ttl_s))
        elif topic is Topic.RISK_METER and isinstance(event, RiskMeter):
            self._meter, self._meter_mono = event, now

    def _active(self, now: float) -> list[_Sig]:
        return [s for s in self._signals if s.expires_mono > now]

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        active = self._active(now)
        meter_fresh = self._meter is not None and now - self._meter_mono <= METER_STALE_S
        if meter_fresh and self._meter is not None:
            level, score, top = self._meter.level, self._meter.score, list(self._meter.top_sources)
            source = "meter"
        else:
            # No fresh meter: fall back to the strongest live signal (each decays by ttl).
            score = max((s.signal.score for s in active), default=0.0)
            level, top = level_for(score), []
            source = "signals" if active else "idle"
        signals = [
            {
                "source": s.signal.source,
                "category": s.signal.category.value,
                "score": round(s.signal.score, 3),
                "label": s.signal.label,
                "at": s.signal.wall_ts,
                "expires_in_s": round(max(0.0, s.expires_mono - now), 1),
            }
            for s in sorted(active, key=lambda s: -s.signal.score)
        ]
        return {
            "level": API_LEVEL[level],  # ok | watch | warn | danger
            "meter_level": level,  # the guard's own name: green | yellow | orange | red
            "score": round(score, 3),
            "top_sources": top,
            "signals": signals,
            "basis": source,
            "meter_age_s": None if self._meter is None else round(now - self._meter_mono, 1),
        }

    def reset(self) -> None:
        self._signals.clear()
        self._meter = None

    @staticmethod
    def subscribe(bus: BusClient) -> Subscription:
        return bus.subscribe([Topic.RISK, Topic.RISK_METER])

    async def run(self, sub: Subscription) -> None:
        try:
            async for topic, event in sub:
                self.observe(topic, event)
        except asyncio.CancelledError:
            raise
        finally:
            sub.close()


def signal_ts(sig: RiskSignal) -> float | None:
    return parse_ts(sig.wall_ts)
