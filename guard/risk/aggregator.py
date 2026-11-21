"""Risk aggregator (spec 5.5): decayed signals combined by a bounded noisy-OR.

Time is stream time (seconds, the same PTS axis as the signals), passed in by the caller, so the
whole thing is deterministic on a virtual clock. Signal contents are codes only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from contracts.config import GuardConfig
from contracts.enums import Modality
from contracts.events import RiskMeter, RiskSignal, SafetyEvent

from .meter import LevelTracker

DEFAULT_WEIGHTS = {"fingerprint": 1.0, "slur": 0.9, "risky_text": 0.6, "profanity": 0.5, "music": 0.35}
ACCUMULATING = frozenset({"profanity", "slur"})  # discrete events add up; the others refresh one slot
MAX_PER_SOURCE = 64
TOP_SOURCES = 3


@dataclass(slots=True)
class _Live:
    sig: RiskSignal
    t: float  # stream time of the signal


@dataclass(frozen=True, slots=True)
class Update:
    meter: RiskMeter
    warnings: tuple[SafetyEvent, ...] = ()


@dataclass(slots=True)
class _State:
    live: dict[str, list[_Live]] = field(default_factory=dict)


def _utc_iso(_t: float) -> str:
    return datetime.now(UTC).isoformat()


class RiskAggregator:
    def __init__(
        self,
        cfg: GuardConfig | None = None,
        *,
        wall_ts_fn: Callable[[float], str] = _utc_iso,
        session_id: str = "session",
    ) -> None:
        cfg = cfg or GuardConfig()
        self.weights = {**DEFAULT_WEIGHTS, **cfg.weights}
        self.cooldown_s = cfg.warn_cooldown_s
        self.period_s = 1.0 / cfg.meter_hz
        self._wall = wall_ts_fn
        self._session = session_id
        self._st = _State()
        self._tracker = LevelTracker()
        self._last_pub: float | None = None
        self._last_warn: dict[str, float] = {}
        self._warn_seq = 0

    # ------------------------------------------------------------------ maths
    @staticmethod
    def contribution(sig: RiskSignal, t_sig: float, now: float) -> float:
        age = max(0.0, now - t_sig)
        if age > sig.ttl_s:
            return 0.0
        return sig.score * 2.0 ** (-age / (sig.ttl_s / 3.0))

    def _weighted(self, item: _Live, now: float) -> float:
        return self.weights.get(item.sig.source, 0.5) * self.contribution(item.sig, item.t, now)

    def _prune(self, now: float) -> None:
        for src in list(self._st.live):
            kept = [i for i in self._st.live[src] if now - i.t <= i.sig.ttl_s]
            if kept:
                self._st.live[src] = kept
            else:
                del self._st.live[src]

    def score(self, now: float) -> float:
        prod = 1.0
        for items in self._st.live.values():
            for item in items:
                prod *= 1.0 - min(1.0, self._weighted(item, now))
        return min(1.0, max(0.0, 1.0 - prod))

    def _ranked(self, now: float) -> list[tuple[float, _Live]]:
        out = [(self._weighted(i, now), i) for items in self._st.live.values() for i in items]
        out.sort(key=lambda p: (-p[0], p[1].sig.source))
        return out

    # ------------------------------------------------------------------ input
    def _store(self, sig: RiskSignal, now: float) -> None:
        t = now if sig.pts is None else sig.pts
        item = _Live(sig, t)
        if sig.source in ACCUMULATING:
            lst = self._st.live.setdefault(sig.source, [])
            lst.append(item)
            if len(lst) > MAX_PER_SOURCE:
                lst.sort(key=lambda i: self._weighted(i, now))
                del lst[0]
            return
        key = (sig.source, sig.label)
        slot = self._st.live.setdefault(sig.source, [])
        for k, old in enumerate(slot):
            if (old.sig.source, old.sig.label) == key:
                if self._weighted(item, now) >= self._weighted(old, now):
                    slot[k] = item
                return
        slot.append(item)
        if len(slot) > MAX_PER_SOURCE:
            slot.sort(key=lambda i: self._weighted(i, now))
            del slot[0]

    def add(self, sig: RiskSignal, now: float) -> Update:
        """Take one signal and publish the meter immediately."""
        return self.add_many([sig], now)

    def add_many(self, sigs: list[RiskSignal], now: float) -> Update:
        for sig in sigs:
            self._store(sig, now)
        return self._publish(now, allow_warn=True)

    def tick(self, now: float) -> Update | None:
        """Call as often as you like; returns an update when the 2 Hz publish is due."""
        if self._last_pub is not None and now - self._last_pub < self.period_s - 1e-9:
            return None
        return self._publish(now, allow_warn=False)

    # ------------------------------------------------------------------ output
    def _publish(self, now: float, *, allow_warn: bool) -> Update:
        self._prune(now)
        score = self.score(now)
        before = self._tracker.index
        level = self._tracker.update(score)
        ranked = self._ranked(now)
        top: list[str] = []
        for _, item in ranked:
            if item.sig.source not in top:
                top.append(item.sig.source)
        top = top[:TOP_SOURCES] if score > 0 else []
        warnings: tuple[SafetyEvent, ...] = ()
        if self._tracker.index > before and ranked:
            warnings = self._warn(ranked[0][1], score, now)
        self._last_pub = now
        meter = RiskMeter(wall_ts=self._wall(now), level=level, score=round(score, 6), top_sources=tuple(top))
        return Update(meter, warnings)

    def _warn(self, item: _Live, score: float, now: float) -> tuple[SafetyEvent, ...]:
        src = item.sig.source
        last = self._last_warn.get(src)
        if last is not None and now - last < self.cooldown_s:
            return ()
        self._last_warn[src] = now
        self._warn_seq += 1
        return (
            SafetyEvent(
                event_id=f"guard-warn-{self._warn_seq}",
                session_id=self._session,
                wall_ts=self._wall(now),
                pts=now,
                modality=Modality.GUARD,
                category=item.sig.category,
                action="warned",
                confidence=round(min(1.0, score), 6),
                bbox=None,
                duration_ms=None,
                source="guard_risk",
                detail_code=src,
            ),
        )

    @property
    def level(self) -> str:
        return self._tracker.level
