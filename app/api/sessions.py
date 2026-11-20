"""Session lifecycle bookkeeping (T1.6 minimum; T6.1 adds orchestration and the report).

The engine is the source of truth: ``engine.status`` says whether it is starting, waiting for
ingest, live or stopping. This object remembers what the *API* asked for (``start`` / ``stop``)
and turns the pair into the state shown to the UI:

    idle -> starting -> waiting_ingest -> live -> stopping -> ended
                              \\-> failed (engine error or start timeout)
                              \\-> engine_lost (status heartbeats stopped after the stream was live)

There is only one active session. Nothing here touches hold: hold works in every state.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from contracts import EngineStatus

SessionState = Literal[
    "idle", "starting", "waiting_ingest", "live", "stopping", "ended", "failed", "engine_lost"
]
ACTIVE_STATES: frozenset[str] = frozenset({"starting", "waiting_ingest", "live", "stopping", "engine_lost"})

_ENGINE_TO_STATE: dict[str, SessionState] = {
    "starting": "starting",
    "ready": "starting",
    "waiting_ingest": "waiting_ingest",
    "live": "live",
    "stopping": "stopping",
    "error": "failed",
}


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    started_at: str
    started_mono: float
    delay_seconds: float
    profile: str
    destinations: dict[str, bool]
    stop_requested: bool = False
    seen_live: bool = False


@dataclass(slots=True)
class SessionManager:
    start_timeout_s: float = 30.0
    record: SessionRecord | None = field(default=None)

    def start(self, delay_seconds: float, profile: str, destinations: dict[str, bool]) -> SessionRecord:
        self.record = SessionRecord(
            session_id=secrets.token_urlsafe(9),
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            started_mono=time.monotonic(),
            delay_seconds=delay_seconds,
            profile=profile,
            destinations=dict(destinations),
        )
        return self.record

    def stop(self) -> None:
        if self.record is not None:
            self.record.stop_requested = True

    def state(self, engine: EngineStatus | None) -> SessionState:
        rec = self.record
        mapped = _ENGINE_TO_STATE.get(engine.state) if engine is not None else None
        if rec is None:
            # Engine started outside the API (scripts/run_engine): report what it says, but a
            # bare "ready" engine is idle from the streamer's point of view.
            if mapped in (None, "starting"):
                return "idle"
            return mapped
        if mapped == "live":
            rec.seen_live = True
        if rec.stop_requested:
            if mapped in ("live", "waiting_ingest", "stopping"):
                return "stopping"
            return "ended"
        if mapped is not None:
            return mapped
        # No fresh heartbeat while a session is supposed to be running.
        if rec.seen_live:
            return "engine_lost"
        if time.monotonic() - rec.started_mono > self.start_timeout_s:
            return "failed"
        return "starting"

    def is_active(self, engine: EngineStatus | None) -> bool:
        return self.state(engine) in ACTIVE_STATES
