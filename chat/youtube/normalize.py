"""``liveChatMessages`` items to ``ChatMessage``.

Message ids must satisfy the contract's id pattern. YouTube ids normally do; if one does not, a stable
hashed id (``h:<hex>``) is used and the poller remembers the original for platform actions.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any

from contracts import ChatMessage
from contracts.privacy import ID_RE

# snippet.type values that carry viewer text. Super Chat / membership are treated as messages (amount ignored).
TEXT_TYPES = frozenset(
    {
        "textMessageEvent",
        "superChatEvent",
        "superStickerEvent",
        "newSponsorEvent",
        "memberMilestoneChatEvent",
        "membershipGiftingEvent",
        "giftMembershipReceivedEvent",
    }
)
CHAT_ENDED_TYPE = "chatEndedEvent"


def item_type(item: dict[str, Any]) -> str:
    snippet = item.get("snippet")
    kind = snippet.get("type") if isinstance(snippet, dict) else None
    return kind if isinstance(kind, str) else ""


class IdMap:
    """Bounded map from hashed ids back to the platform's original ids (used by actions)."""

    def __init__(self, capacity: int = 5000) -> None:
        self._capacity = capacity
        self._map: OrderedDict[str, str] = OrderedDict()

    def safe(self, raw: str) -> str:
        if ID_RE.match(raw):
            return raw
        safe = "h:" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]
        self._map[safe] = raw
        self._map.move_to_end(safe)
        while len(self._map) > self._capacity:
            self._map.popitem(last=False)
        return safe

    def resolve(self, safe: str) -> str:
        return self._map.get(safe, safe)


def extract_text(snippet: dict[str, Any]) -> str:
    details = snippet.get("textMessageDetails")
    if isinstance(details, dict) and isinstance(details.get("messageText"), str):
        return details["messageText"]
    chat = snippet.get("superChatDetails")
    if isinstance(chat, dict) and isinstance(chat.get("userComment"), str):
        return chat["userComment"]
    sticker = snippet.get("superStickerDetails")
    if isinstance(sticker, dict):  # stickers have no text
        return ""
    display = snippet.get("displayMessage")
    return display if isinstance(display, str) else ""


def normalize_item(
    item: dict[str, Any], *, live_chat_id: str, received_at: float, ids: IdMap | None = None
) -> ChatMessage | None:
    """Return a ``ChatMessage`` for a viewer-text item, else ``None`` (deletions, bans, system events,
    empty text, or malformed items). Never raises on bad platform data."""
    if not isinstance(item, dict) or item_type(item) not in TEXT_TYPES:
        return None
    ids = ids or IdMap()
    snippet = item["snippet"]
    author = item.get("authorDetails")
    raw_id = item.get("id")
    if not isinstance(author, dict) or not isinstance(raw_id, str) or not raw_id:
        return None
    channel = author.get("channelId") or snippet.get("authorChannelId")
    text = extract_text(snippet)
    if not isinstance(channel, str) or not text.strip():
        return None
    display = author.get("displayName")
    try:
        return ChatMessage(
            platform="youtube",
            message_id=ids.safe(raw_id),
            channel_id=ids.safe(live_chat_id),
            author_id=ids.safe(channel),
            author_display=display if isinstance(display, str) else "",
            text=text,
            received_at=received_at,
            is_broadcaster=author.get("isChatOwner") is True,
            is_moderator=author.get("isChatModerator") is True,
        )
    except ValueError:
        return None  # contract validation failed; the message is dropped, never logged
