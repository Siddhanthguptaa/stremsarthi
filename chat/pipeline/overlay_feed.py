"""Overlay feed contract (docs/specs/chat.md 4 and 5.5).

The streamer overlay is fed by exactly two things, both leaving this process on the bus:

* ``chat.allowed`` (``ChatMessage``): the ONLY text the streamer ever sees. It is published once per
  message, only when the message's final verdict is ALLOW, or when a moderator approves a held message,
  or when a provisional hide is downgraded to ALLOW by the LLM stage. A message that is hidden, held,
  deleted or timed out never appears here. Messages from the broadcaster and moderators are not hidden.
* ``chat.verdict`` (``ChatVerdict``, no text): from it a consumer derives ``hidden_count``. This class
  keeps the same number locally (``hidden_count``) so the rule is defined in one place:
  a message counts as hidden while its latest verdict is not ALLOW. A later ALLOW (downgrade or
  moderator approval) takes it back out. A provisional ``pending_llm`` hide counts as hidden.

Never any text in logs; the tracking maps hold ids only.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass

from chat.common import ChatPublisher, log_event
from contracts import ChatAction, ChatMessage, ChatVerdict

_LOG = logging.getLogger("chat.overlay")
Key = tuple[str, str]


@dataclass(frozen=True, slots=True)
class OverlayState:
    shown: int
    hidden_count: int


class OverlayFeed:
    def __init__(self, publisher: ChatPublisher, *, track_max: int = 20_000) -> None:
        self._publisher = publisher
        self._track_max = track_max
        self._shown: OrderedDict[Key, None] = OrderedDict()
        self._hidden: OrderedDict[Key, bool] = OrderedDict()
        self._hidden_count = 0
        self._shown_total = 0

    def __repr__(self) -> str:
        return f"OverlayFeed(shown={self._shown_total}, hidden={self._hidden_count})"

    @property
    def hidden_count(self) -> int:
        return self._hidden_count

    def state(self) -> OverlayState:
        return OverlayState(self._shown_total, self._hidden_count)

    def release(self, message: ChatMessage) -> bool:
        """Publish ``message`` on ``chat.allowed`` unless it was already shown. Returns True if published."""
        key = (message.platform, message.message_id)
        if key in self._shown:
            return False
        self._shown[key] = None
        while len(self._shown) > self._track_max:
            self._shown.popitem(last=False)
        try:
            self._publisher.allowed(message)
        except Exception as exc:  # a bus fault must never stop the chat process
            log_event(_LOG, "overlay_publish_failed", level=logging.ERROR, error=type(exc).__name__)
            return False
        self._shown_total += 1
        return True

    def note_verdict(self, verdict: ChatVerdict) -> None:
        """Keep ``hidden_count`` in step with the latest verdict of each message."""
        key = (verdict.platform, verdict.message_id)
        hidden = verdict.action is not ChatAction.ALLOW
        was = self._hidden.pop(key, False)
        self._hidden[key] = hidden
        while len(self._hidden) > self._track_max:
            self._hidden.popitem(last=False)
        self._hidden_count += int(hidden) - int(was)
