"""Per-session record that the safety report is built from (RAM, plus ``events.jsonl`` on disk).

Only privacy-reduced facts are kept: ``SafetyEvent`` fields (category, time, confidence, box, fixed
codes), hold intervals, the strike-guard warnings (source, label code, score), chat verdict counts
(action, category, platform result: never message text, ids or authors) and engine timing samples.
``events.jsonl`` carries ``SafetyEvent`` wire objects only (spec 5.7); it is appended as events arrive
(an update with ``duration_ms`` appends the same ``event_id`` again; readers keep the last line).
A failed write (disk full, no permission) drops file output for that session, keeps RAM, and adds a
fixed note to the report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contracts import (
    ChatAction,
    ChatVerdict,
    EngineStatus,
    HoldCommand,
    HoldSource,
    RiskSignal,
    SafetyEvent,
    Topic,
    to_wire,
)

from .bus import BusClient, Subscription
from .events import SafetyEventStore
from .risk import LEVELS, level_for

log = logging.getLogger("streamsarthi.api.recorder")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{6,64}$")
MAX_EVENTS = 20_000
MAX_WARNINGS = 100
MAX_SAMPLES = 7_200  # 2 h of 1 Hz engine.status
MAX_CHAT_IDS = 20_000
WARN_SCORE = 0.5  # a risk signal at orange or above is a "warning" in the report


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class HoldInterval:
    start_ts: str
    end_ts: str | None
    sources: set[str] = field(default_factory=set)


@dataclass(slots=True)
class SessionLog:
    session_id: str
    started_at: str
    profile: str
    delay_seconds: float
    events: SafetyEventStore = field(default_factory=lambda: SafetyEventStore(MAX_EVENTS))
    ended_at: str | None = None
    holds: list[HoldInterval] = field(default_factory=list)
    active_hold_sources: set[str] = field(default_factory=set)
    risk_max_idx: int = 0
    warnings: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_WARNINGS))
    chat_last: dict[str, tuple[str, tuple[str, ...], str]] = field(default_factory=dict)
    delay_samples: deque[float] = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    fps_samples: deque[float] = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    protect_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    jsonl: Path | None = None
    jsonl_failed: bool = False

    def chat_totals(self) -> dict[str, Any]:
        by_action: Counter[str] = Counter()
        by_category: Counter[str] = Counter()
        platform: Counter[str] = Counter()
        for action, cats, result in self.chat_last.values():
            by_action[action] += 1
            for c in cats:
                by_category[c] += 1
            if result in ("done", "failed", "unsupported"):
                platform[result] += 1
        return {
            "by_action": dict(sorted(by_action.items())),
            "by_category": dict(sorted(by_category.items())),
            "platform_actions": {k: platform.get(k, 0) for k in ("done", "failed", "unsupported")},
        }


class SessionRecorder:
    """Records the active session. One session at a time; finished logs stay until ``forget``."""

    def __init__(self, sessions_dir: Path | None) -> None:
        self.sessions_dir = sessions_dir
        self.current: SessionLog | None = None

    # --- lifecycle ---------------------------------------------------------------------------
    def begin(self, session_id: str, started_at: str, profile: str, delay_seconds: float) -> SessionLog:
        log_ = SessionLog(session_id, started_at, profile, delay_seconds)
        if self.sessions_dir is not None and SESSION_ID_RE.match(session_id):
            path = self.sessions_dir / session_id / "events.jsonl"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(mode=0o600)
                log_.jsonl = path
            except OSError:
                log_.jsonl_failed = True
                log.warning("events.jsonl could not be created; the report stays in RAM only")
        self.current = log_
        return log_

    def end(self) -> SessionLog | None:
        """Close the active session: open holds end now, the record is returned for the report."""
        cur = self.current
        if cur is None:
            return None
        ts = now_iso()
        cur.ended_at = ts
        for h in cur.holds:
            if h.end_ts is None:
                h.end_ts = ts
        cur.active_hold_sources.clear()
        self.current = None
        return cur

    def delete_files(self, session_id: str) -> bool:
        if self.sessions_dir is None or not SESSION_ID_RE.match(session_id):
            return False
        target = self.sessions_dir / session_id
        try:
            if target.is_dir():
                shutil.rmtree(target)
                return True
        except OSError:
            log.warning("session data could not be removed")
        return False

    # --- observe -----------------------------------------------------------------------------
    def observe(self, topic: Topic, event: Any) -> None:
        cur = self.current
        if cur is None:
            return
        if topic is Topic.SAFETY_EVENT and isinstance(event, SafetyEvent):
            self._event(cur, event)
        elif topic is Topic.HOLD and isinstance(event, HoldCommand):
            self._hold(cur, event)
        elif topic is Topic.RISK and isinstance(event, RiskSignal):
            self._risk(cur, event)
        elif topic is Topic.CHAT_VERDICT and isinstance(event, ChatVerdict):
            self._chat(cur, event)
        elif topic is Topic.ENGINE_STATUS and isinstance(event, EngineStatus):
            self._status(cur, event)

    def _event(self, cur: SessionLog, event: SafetyEvent) -> None:
        cur.events.upsert(event)
        if cur.jsonl is None or cur.jsonl_failed:
            return
        try:
            with cur.jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(to_wire(event), separators=(",", ":")) + "\n")
        except OSError:
            cur.jsonl_failed = True
            log.warning("events.jsonl write failed; keeping events in RAM only")

    @staticmethod
    def _hold(cur: SessionLog, cmd: HoldCommand) -> None:
        src = cmd.source.value
        if cmd.active:
            if not cur.active_hold_sources:
                cur.holds.append(HoldInterval(now_iso(), None, set()))
            cur.active_hold_sources.add(src)
            cur.holds[-1].sources.add(src)
        else:
            cur.active_hold_sources.discard(src)  # a release clears only its own source
            if not cur.active_hold_sources and cur.holds and cur.holds[-1].end_ts is None:
                cur.holds[-1].end_ts = now_iso()

    @staticmethod
    def _risk(cur: SessionLog, sig: RiskSignal) -> None:
        cur.risk_max_idx = max(cur.risk_max_idx, LEVELS.index(level_for(sig.score)))
        if sig.score >= WARN_SCORE:
            cur.warnings.append(
                {"ts": sig.wall_ts, "source": sig.source, "label": sig.label, "score": round(sig.score, 3)}
            )

    @staticmethod
    def _chat(cur: SessionLog, v: ChatVerdict) -> None:
        if len(cur.chat_last) >= MAX_CHAT_IDS and v.message_id not in cur.chat_last:
            return
        cur.chat_last[v.message_id] = (
            v.action.value if isinstance(v.action, ChatAction) else str(v.action),
            tuple(c.value for c in v.categories),
            v.platform_result,
        )

    @staticmethod
    def _status(cur: SessionLog, st: EngineStatus) -> None:
        if st.delay_s_p50 is not None:
            cur.delay_samples.append(st.delay_s_p50)
        if st.fps is not None:
            cur.fps_samples.append(st.fps)
        cur.protect_seconds = max(cur.protect_seconds, st.protect_seconds)
        # A hold the engine applied on its own (transport/egress loss) shows in the hold list too.
        auto = {s.value for s in st.hold_sources if s in (HoldSource.AUTO_TRANSPORT, HoldSource.AUTO_EGRESS)}
        for src in auto - cur.active_hold_sources:
            if not cur.active_hold_sources:
                cur.holds.append(HoldInterval(now_iso(), None, set()))
            cur.active_hold_sources.add(src)
            cur.holds[-1].sources.add(src)
        for src in (cur.active_hold_sources & {"auto_transport", "auto_egress"}) - auto:
            cur.active_hold_sources.discard(src)
            if not cur.active_hold_sources and cur.holds and cur.holds[-1].end_ts is None:
                cur.holds[-1].end_ts = now_iso()

    @staticmethod
    def subscribe(bus: BusClient) -> Subscription:
        return bus.subscribe(
            [Topic.SAFETY_EVENT, Topic.HOLD, Topic.RISK, Topic.CHAT_VERDICT, Topic.ENGINE_STATUS]
        )

    async def run(self, sub: Subscription) -> None:
        try:
            async for topic, event in sub:
                try:
                    self.observe(topic, event)
                except Exception:  # one bad event must not stop recording
                    log.exception("recorder failed on %s", topic.value)
        except asyncio.CancelledError:
            raise
        finally:
            sub.close()
