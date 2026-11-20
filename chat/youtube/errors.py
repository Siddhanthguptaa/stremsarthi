from __future__ import annotations

from chat.common.errors import ChatError


class YouTubeError(ChatError):
    """Fixed-code YouTube error (``oauth_denied``, ``yt_auth_failed``, ...)."""
