"""Chat contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .enums import Category, ChatAction
from .privacy import validate_instance

_CHAT_CATEGORIES = {
    Category.CHAT_ABUSE,
    Category.CHAT_DOXXING,
    Category.CHAT_SCAM_LINK,
    Category.CHAT_FAKE_UPI,
}


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Transient. ``text`` and ``author_display`` are free text: never log a flagged message."""

    platform: Literal["youtube", "twitch"]
    message_id: str
    channel_id: str
    author_id: str
    author_display: str
    text: str
    received_at: float  # wall-clock epoch seconds
    is_broadcaster: bool = False
    is_moderator: bool = False

    def __post_init__(self) -> None:
        if self.platform not in ("youtube", "twitch"):
            raise ValueError("invalid ChatMessage.platform")
        validate_instance(self)

    def __repr__(self) -> str:  # never print message text
        return f"ChatMessage(platform={self.platform!r}, message_id={self.message_id!r})"


@dataclass(frozen=True, slots=True)
class ChatVerdict:
    platform: Literal["youtube", "twitch"]
    message_id: str
    action: ChatAction
    categories: tuple[Category, ...]
    confidence: float
    reason: str  # code
    stage: Literal["rules", "llm", "model"]
    latency_ms: float
    timeout_seconds: int | None = None
    platform_result: Literal["pending", "done", "unsupported", "failed"] = "pending"

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("ChatVerdict.confidence must be within 0..1")
        for c in self.categories:
            if c not in _CHAT_CATEGORIES:
                raise ValueError(f"{c} is not a chat category")
        if self.action is ChatAction.TIMEOUT and not self.timeout_seconds:
            raise ValueError("TIMEOUT verdict needs timeout_seconds")
        if self.action is not ChatAction.ALLOW and not self.categories:
            raise ValueError("a non-ALLOW verdict needs at least one category")
        if self.stage not in ("rules", "llm", "model"):
            raise ValueError("invalid ChatVerdict.stage")
        if self.platform_result not in ("pending", "done", "unsupported", "failed"):
            raise ValueError("invalid ChatVerdict.platform_result")
        validate_instance(self)


@dataclass(frozen=True, slots=True)
class ModQueueItem:
    """A flagged message awaiting a moderator. Carries text, so it is moderator-role only
    (enforced by the API) and RAM-only (never persisted)."""

    message: ChatMessage
    verdict: ChatVerdict
    enqueued_at: float
    state: Literal["pending", "approved", "deleted", "timed_out", "dismissed"] = "pending"

    def __post_init__(self) -> None:
        if self.state not in ("pending", "approved", "deleted", "timed_out", "dismissed"):
            raise ValueError("invalid ModQueueItem.state")
        if (self.message.platform, self.message.message_id) != (
            self.verdict.platform,
            self.verdict.message_id,
        ):
            raise ValueError("ModQueueItem message and verdict must refer to the same message")


@dataclass(frozen=True, slots=True)
class ModAction:
    message_id: str
    platform: Literal["youtube", "twitch"]
    action: Literal["approve", "delete", "timeout", "dismiss"]
    timeout_seconds: int | None
    actor: str  # opaque hash

    def __post_init__(self) -> None:
        if self.platform not in ("youtube", "twitch"):
            raise ValueError("invalid ModAction.platform")
        if self.action not in ("approve", "delete", "timeout", "dismiss"):
            raise ValueError("invalid ModAction.action")
        if self.action == "timeout" and not self.timeout_seconds:
            raise ValueError("a timeout ModAction needs timeout_seconds")
        validate_instance(self)
