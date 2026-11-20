from __future__ import annotations

from chat.common.errors import ChatError


class TwitchError(ChatError):
    """Fixed-code Twitch error. Never carries a remote response body."""


class InvalidToken(TwitchError):
    """The validation endpoint (or Helix) definitively rejected the access token."""

    def __init__(self) -> None:
        super().__init__("twitch_auth_failed")
