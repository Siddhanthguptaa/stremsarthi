"""Allowlisted YouTube/Google routes on the shared fixed-origin client."""

from __future__ import annotations

import re
from typing import Any

import aiohttp

from chat.common.http import FixedOriginClient

ORIGINS = {"oauth": "https://oauth2.googleapis.com", "api": "https://www.googleapis.com"}
ROUTES = {
    ("POST", "/token"): "oauth",
    ("GET", "/youtube/v3/liveBroadcasts"): "api",
    ("GET", "/youtube/v3/liveChat/messages"): "api",
    ("DELETE", "/youtube/v3/liveChat/messages"): "api",
    ("POST", "/youtube/v3/liveChat/bans"): "api",
}
_REASON_RE = re.compile(r"^[A-Za-z0-9_]{1,48}$")


def make_http(session: aiohttp.ClientSession, *, loopback_origin: str | None = None) -> FixedOriginClient:
    # liveChatMessages.list with maxResults=200 is far larger than the 64 KiB Twitch default.
    return FixedOriginClient(
        session, origins=ORIGINS, routes=ROUTES, loopback_origin=loopback_origin, max_bytes=2 * 1024 * 1024
    )


def api_error_reason(body: dict[str, Any]) -> str:
    """The Google API error ``reason`` (``quotaExceeded``, ``rateLimitExceeded``, ``liveChatEnded`` ...),
    or ``unknown``. Message text from the response is never kept."""
    try:
        errors = body["error"]["errors"]
        reason = errors[0]["reason"]
    except (KeyError, IndexError, TypeError):
        return "unknown"
    return reason if isinstance(reason, str) and _REASON_RE.match(reason) else "unknown"
