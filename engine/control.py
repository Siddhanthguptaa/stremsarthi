"""Explicit stream protection state: per-source holds, coverage, retroactive-hold windows.

Adapted from IRL Shield ``shield/control.py`` (MIT, Copyright (c) 2026 Elan Nash; licence text in
``engine/THIRD_PARTY_LICENSES/irl-shield/LICENSE``). Changes: holds are keyed by
``contracts.HoldSource``; a public ``snapshot()`` replaces reads of the private ``_holds``;
retro-hold bookkeeping, a lock, and a bounded transition ledger were added.

Priority is ``HOLD > PROTECT > LIVE``. Coverage decides LIVE or PROTECT; any active hold forces
HOLD. Releasing one source never clears another.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from contracts import HoldSource

LIVE = "LIVE"
PROTECT = "PROTECT"
HOLD = "HOLD"

# Sources a person triggers on purpose. Only these reach back into the delay buffer.
RETRO_SOURCES: frozenset[HoldSource] = frozenset(
    {HoldSource.HOTKEY, HoldSource.API, HoldSource.APP, HoldSource.CHAT}
)


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    state: str
    reason: str
    hold_sources: tuple[HoldSource, ...]  # in assertion order, newest last
    coverage_ok: bool
    coverage_reason: str

    @property
    def hold_active(self) -> bool:
        return bool(self.hold_sources)


@dataclass(frozen=True, slots=True)
class RetroWindow:
    """A hold asserted at wall time ``t_to`` that reaches back to ``t_from`` (both monotonic)."""

    source: HoldSource
    t_from: float
    t_to: float


class Control:
    """Coverage controls LIVE/PROTECT; any named hold independently forces HOLD.

    Callers supply event times. The most recently asserted active hold supplies the reason,
    and only state changes append to the transition ledger.
    """

    def __init__(self, retro_seconds: float = 0.0, max_transitions: int = 1024) -> None:
        if retro_seconds < 0:
            raise ValueError("retro_seconds must be >= 0")
        self.retro_seconds = float(retro_seconds)
        self._lock = threading.RLock()
        self._state = PROTECT
        self._reason = "startup"
        self._coverage_ok = False
        self._coverage_reason = "startup"
        self._holds: dict[HoldSource, str] = {}
        self._transitions: deque[dict[str, object]] = deque(maxlen=max_transitions)
        self._retro: list[RetroWindow] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def transitions(self) -> list[dict[str, object]]:
        with self._lock:
            return [dict(event) for event in self._transitions]

    def snapshot(self) -> ControlSnapshot:
        """Public, immutable view for the status endpoint and the engine. Never read ``_holds``."""
        with self._lock:
            return ControlSnapshot(
                self._state,
                self._reason,
                tuple(self._holds),
                self._coverage_ok,
                self._coverage_reason,
            )

    def is_held(self, source: HoldSource | None = None) -> bool:
        with self._lock:
            return bool(self._holds) if source is None else HoldSource(source) in self._holds

    def set_hold(
        self,
        source: HoldSource | str,
        active: bool,
        *,
        reason: str | None = None,
        at: float = 0.0,
    ) -> str:
        """Assert or release one source. Re-asserting moves it to the front for the reason code.

        A person-triggered source (``RETRO_SOURCES``) that goes inactive -> active records a
        ``RetroWindow`` reaching back ``retro_seconds``; fetch it with ``take_retro_windows``.
        """
        src = HoldSource(source)
        with self._lock:
            was_active = src in self._holds
            self._holds.pop(src, None)
            if active:
                self._holds[src] = reason or src.value
                if not was_active and src in RETRO_SOURCES and self.retro_seconds > 0:
                    self._retro.append(RetroWindow(src, at - self.retro_seconds, at))
            return self._settle(at)

    def take_retro_windows(self) -> list[RetroWindow]:
        """Hand over (and forget) the retro windows recorded since the last call."""
        with self._lock:
            windows, self._retro = self._retro, []
            return windows

    def update_coverage(self, ok: bool, reason: str, at: float = 0.0) -> str:
        with self._lock:
            self._coverage_ok = ok
            self._coverage_reason = reason
            return self._settle(at)

    def _settle(self, at: float) -> str:
        if self._holds:
            state, reason = HOLD, next(reversed(self._holds.values()))
        else:
            state = LIVE if self._coverage_ok else PROTECT
            reason = self._coverage_reason
        if state != self._state:
            self._transitions.append({"from": self._state, "to": state, "reason": reason, "at": float(at)})
        self._state, self._reason = state, reason
        return state
