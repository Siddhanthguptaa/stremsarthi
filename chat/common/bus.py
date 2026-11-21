"""Typed publisher for the chat topics. The chat process talks to the rest of the system only here."""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, datetime

from contracts import Category, ChatMessage, ChatVerdict, EventBus, Modality, SafetyEvent, Topic

from .clock import Clock, SystemClock
from .connector import StatusCallback
from .logsafe import hash_id, log_event

_LOG = logging.getLogger("chat.bus")

# Connector status codes that become a SafetyEvent(detail_code=<code>). Everything else is only logged.
REPORTED_STATUS_CODES = frozenset(
    {
        "yt_quota_low",
        "yt_quota_exhausted",
        "yt_actions_blocked",
        "yt_auth_failed",
        "yt_chat_ended",
        "twitch_disconnected",
        "twitch_reconnected",
        "twitch_auth_failed",
        "twitch_subscription_revoked",
        "twitch_actions_blocked",
    }
)


_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
DEFAULT_SESSION_ID = "chat"


def session_id_from_env(default: str = DEFAULT_SESSION_ID) -> str:
    """``STREAMSARTHI_SESSION_ID`` (set by the API for the chat process) if it is at most 64 of ``[A-Za-z0-9_-]``."""
    value = os.environ.get("STREAMSARTHI_SESSION_ID", "")
    return value if _SESSION_RE.fullmatch(value) else default


class ChatPublisher:
    """``allowed`` -> ``chat.allowed`` (the only text the streamer overlay ever sees), ``verdict`` ->
    ``chat.verdict`` (no text), ``safety`` -> ``safety.event`` (codes only)."""

    def __init__(self, bus: EventBus, *, session_id: str = "chat", clock: Clock | None = None) -> None:
        self._bus = bus
        self._session_id = session_id
        self._clock: Clock = clock or SystemClock()

    def allowed(self, message: ChatMessage) -> None:
        self._bus.publish(Topic.CHAT_ALLOWED, message)

    def verdict(self, verdict: ChatVerdict) -> None:
        self._bus.publish(Topic.CHAT_VERDICT, verdict)
        log_event(
            _LOG,
            "chat_verdict",
            level=logging.DEBUG,
            mid=hash_id(verdict.message_id),
            action=verdict.action.value,
            stage=verdict.stage,
            reason=verdict.reason,
            conf=round(verdict.confidence, 3),
            ms=round(verdict.latency_ms, 1),
        )

    def safety(self, detail_code: str, *, source: str = "chat", confidence: float = 1.0) -> None:
        """Operational event (quota low, platform disconnected). Never carries text or ids."""
        event = SafetyEvent(
            event_id=uuid.uuid4().hex,
            session_id=self._session_id,
            wall_ts=datetime.fromtimestamp(self._clock.wall(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            pts=None,
            modality=Modality.CHAT,
            # Operational notice (quota low, platform disconnected): not a protection event, so not FAILSAFE.
            category=Category.SYSTEM_STATUS,
            action="warned",
            confidence=confidence,
            bbox=None,
            duration_ms=None,
            source=source,
            detail_code=detail_code,
        )
        self._bus.publish(Topic.SAFETY_EVENT, event)

    def status_sink(self, platform: str) -> StatusCallback:
        """Callback for ``Connector(on_status=...)`` and the governor/events: reportable codes become
        ``SafetyEvent`` s, the rest are ignored here (they are already logged)."""

        def sink(code: str) -> None:
            if code in REPORTED_STATUS_CODES:
                self.safety(code, source=f"chat.{platform}")

        return sink
