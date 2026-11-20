"""EventSub ``channel.chat.message`` policy: validation, de-duplication, generation fencing, mapping to
``ChatMessage``. Pure logic (no I/O), shared by the real transport and offline replay.

Adapted from reference/irl-shield/shield/twitch/events.py (MIT, Copyright (c) 2026 Elan Nash; licence in
``LICENSE.irl-shield``). Kept: endpoint validation, the connection phase machine, generation fencing
(events from a superseded connection are dropped) and the bounded replay cache. Removed: the
shield-mode topics, the `!shield` hold commands and every connection-loss HOLD. A dropped connection
now only reports ``twitch_disconnected`` (docs/specs/chat.md 5.3); a lost chat feed must not freeze the
stream. Added: mapping to ``contracts.ChatMessage`` and dual de-duplication (notification id and chat
message id, because a re-subscription can redeliver the same chat message under a new notification id).
"""

from __future__ import annotations

import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

from chat.common.logsafe import hash_id, log_event
from contracts import ChatMessage

TOPIC = "channel.chat.message"
_ACCEPTING = ("subscribing", "ready", "handover")
_LOG = logging.getLogger("chat.twitch.events")


def websocket_url(url: str, *, mock: bool = False) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("unapproved EventSub endpoint")
    if mock:
        valid = parsed.scheme == "ws" and parsed.hostname == "127.0.0.1" and parsed.port is not None
    else:
        valid = (
            parsed.scheme == "wss"
            and parsed.netloc == "eventsub.wss.twitch.tv"
            and parsed.path in ("", "/", "/ws")
        )
    if not valid:
        raise ValueError("unapproved EventSub endpoint")
    return url


class ChatEvents:
    """One session lifetime, including reconnects. Never recreate it on reconnect.

    Public calls serialise under an ``RLock``. ``accept`` returns an outcome string:
    ``delivered``, ``duplicate``, ``invalid``, ``stale``, ``inactive``, ``ignored`` or ``delivery_failed``.
    """

    def __init__(
        self,
        broadcaster_id: str,
        on_chat: Callable[[ChatMessage], None],
        on_status: Callable[[str], None] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        capacity: int = 4096,
        max_age: float = 600.0,
        mock: bool = False,
    ) -> None:
        if capacity < 1 or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("invalid replay bounds")
        self.broadcaster_id = broadcaster_id
        self._on_chat = on_chat
        self._on_status = on_status
        self._clock = clock
        self._capacity, self._max_age, self._mock = capacity, max_age, mock
        self._lock = RLock()
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._generation = 0
        self._phase = "disconnected"
        self._session_id: str | None = None
        self._revoked = False
        self._outage_reported = False

    # ---- state --------------------------------------------------------------------------------
    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    @property
    def remembered(self) -> int:
        with self._lock:
            return len(self._seen)

    def _status(self, code: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(code)
            except Exception as exc:  # a status sink must never break event handling
                log_event(_LOG, "status_sink_failed", level=logging.WARNING, error=type(exc).__name__)

    # ---- connection lifecycle -----------------------------------------------------------------
    def connection_started(self) -> int:
        """A new socket is being dialled. Supersedes every older generation."""
        with self._lock:
            self._generation += 1
            self._phase = "connecting"
            self._session_id = None
            return self._generation

    def welcome(self, session_id: str, *, generation: int) -> bool:
        with self._lock:
            if generation != self._generation or self._revoked or self._phase != "connecting":
                return False
            if not isinstance(session_id, str) or not session_id:
                self.fault("invalid-welcome")
                return False
            self._session_id, self._phase = session_id, "subscribing"
            return True

    def ready(self, *, generation: int) -> bool:
        """The ``channel.chat.message`` subscription was verified as enabled."""
        with self._lock:
            if generation != self._generation or self._revoked or self._phase != "subscribing":
                return False
            self._phase = "ready"
            if self._outage_reported:
                self._outage_reported = False
                self._status("twitch_reconnected")
            return True

    def reconnect(self, url: str, *, generation: int) -> bool:
        """Twitch sent ``session_reconnect``. Subscriptions carry over to the new socket."""
        with self._lock:
            if generation != self._generation or self._revoked or self._phase != "ready":
                return False
            try:
                websocket_url(url, mock=self._mock)
            except (ValueError, TypeError):
                self.fault("unapproved-reconnect")
                return False
            self._phase = "handover"
            return True

    def handed_over(self, session_id: str, *, generation: int) -> bool:
        with self._lock:
            if generation != self._generation or self._revoked or self._phase != "handover":
                return False
            if not isinstance(session_id, str) or not session_id:
                self.fault("invalid-handover")
                return False
            self._session_id, self._phase = session_id, "ready"
            return True

    def fault(self, reason: str = "disconnected", *, revoked: bool = False, report: bool = True) -> int:
        """The connection is gone (or consent was revoked). No HOLD is asserted; only a status code.

        ``report=False`` is for an intentional shutdown: state changes, but nothing is announced."""
        with self._lock:
            self._generation += 1
            self._revoked = self._revoked or revoked
            self._phase = "revoked" if self._revoked else "disconnected"
            self._session_id = None
            if revoked and report:
                self._status("twitch_subscription_revoked")
            if report and not self._outage_reported:
                self._outage_reported = True
                self._status("twitch_disconnected")
            log_event(_LOG, "twitch_fault", level=logging.WARNING, reason=reason, generation=self._generation)
            return self._generation

    # ---- notifications ------------------------------------------------------------------------
    def accept(self, envelope: dict[str, Any], *, generation: int) -> str:
        with self._lock:
            try:
                metadata, payload = envelope["metadata"], envelope["payload"]
                notification_id = metadata["message_id"]
                subscription, event = payload["subscription"], payload["event"]
                if (
                    metadata["message_type"] != "notification"
                    or metadata["subscription_type"] != TOPIC
                    or metadata["subscription_version"] != "1"
                    or subscription["type"] != TOPIC
                    or subscription["version"] != "1"
                    or subscription["status"] != "enabled"
                    or subscription["condition"]["broadcaster_user_id"] != self.broadcaster_id
                    or event["broadcaster_user_id"] != self.broadcaster_id
                    or not isinstance(notification_id, str)
                    or not 0 < len(notification_id) <= 256
                ):
                    return "invalid"
                # Shared-chat events from another channel are not ours to moderate.
                if event.get("source_broadcaster_user_id") not in (None, "", self.broadcaster_id):
                    return "ignored"
                chat_message_id = event["message_id"]
                author_id = event["chatter_user_id"]
                author_display = event.get("chatter_user_name", "")
                text = event["message"]["text"]
                badges = event.get("badges") or []
                if not all(isinstance(v, str) for v in (chat_message_id, author_id, text)):
                    return "invalid"
                set_ids = {b.get("set_id") for b in badges if isinstance(b, dict)}
            except (KeyError, TypeError, ValueError, AttributeError):
                return "invalid"
            if generation != self._generation:
                return "stale"
            if self._phase not in _ACCEPTING:
                return "inactive"
            now = self._clock()
            self._evict(now)
            keys = (f"n:{notification_id}", f"m:{chat_message_id}")
            if any(key in self._seen for key in keys):
                return "duplicate"
            for key in keys:
                self._seen[key] = now
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)
            try:
                message = ChatMessage(
                    platform="twitch",
                    message_id=chat_message_id,
                    channel_id=self.broadcaster_id,
                    author_id=author_id,
                    author_display=author_display if isinstance(author_display, str) else "",
                    text=text,
                    received_at=now,
                    is_broadcaster=author_id == self.broadcaster_id or "broadcaster" in set_ids,
                    is_moderator="moderator" in set_ids,
                )
            except ValueError:
                return "invalid"  # contract validation failed; the text is never logged
            try:
                self._on_chat(message)
            except Exception as exc:
                log_event(
                    _LOG,
                    "deliver_failed",
                    level=logging.WARNING,
                    mid=hash_id(chat_message_id),
                    error=type(exc).__name__,
                )
                return "delivery_failed"
            return "delivered"

    def _evict(self, now: float) -> None:
        cutoff = now - self._max_age
        while self._seen:
            key, stamp = next(iter(self._seen.items()))
            if stamp > cutoff:
                break
            del self._seen[key]
