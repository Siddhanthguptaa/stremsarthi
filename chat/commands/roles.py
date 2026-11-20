"""Command tokens and platform roles for chat commands (docs/specs/chat.md 5.4).

Roles come from platform data (``ChatMessage.is_broadcaster`` / ``is_moderator``, set by the connector
from badges or ``authorDetails``), never from the message text.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from contracts import ChatMessage

Command = Literal["hold", "unhold"]
Role = Literal["broadcaster", "moderator"]

_TOKENS: dict[str, Command] = {"!hold": "hold", "!unhold": "unhold"}


def parse_command(text: str) -> Command | None:
    """Exact token, case-insensitive, trimmed, no arguments. ``!hold now`` is not a command."""
    return _TOKENS.get(text.strip().lower())


def roles_of(message: ChatMessage) -> frozenset[Role]:
    roles: set[Role] = set()
    if message.is_broadcaster:
        roles.add("broadcaster")
    if message.is_moderator:
        roles.add("moderator")
    return frozenset(roles)


def actor_hash(platform: str, author_id: str) -> str:
    """Opaque actor id for ``HoldCommand.actor``: sha256(platform + author_id)[:16]."""
    return hashlib.sha256(f"{platform}{author_id}".encode("utf-8", "replace")).hexdigest()[:16]
