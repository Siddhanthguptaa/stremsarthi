"""liveChatId lookup (1 quota unit per call), retried every 10 s until a broadcast is live."""

from __future__ import annotations

import logging

from chat.common.clock import Clock, SystemClock
from chat.common.connector import FatalConnectorError, RetryableError
from chat.common.logsafe import log_event

from .client import QuotaDenied, YouTubeClient
from .http import api_error_reason

_LOG = logging.getLogger("chat.youtube.broadcast")


async def lookup_live_chat_id(client: YouTubeClient) -> str | None:
    """One lookup. ``None`` means "no active broadcast yet".

    Uses ``broadcastStatus=active`` alone: the API treats ``mine``, ``id`` and ``broadcastStatus`` as
    mutually exclusive filters, and with OAuth the status filter is scoped to the signed-in channel.
    UNVERIFIED against the live API until real keys exist (docs/specs/chat.md 5.2 listed both)."""
    response = await client.call(
        "GET",
        "/youtube/v3/liveBroadcasts",
        "lookup",
        params={"part": "snippet", "broadcastStatus": "active", "maxResults": 5},
    )
    if response.status == 200:
        for item in response.body.get("items", []):
            chat_id = item.get("snippet", {}).get("liveChatId") if isinstance(item, dict) else None
            if isinstance(chat_id, str) and chat_id:
                return chat_id
        return None
    reason = api_error_reason(response.body)
    if response.status in (401, 403) and reason not in ("rateLimitExceeded", "userRateLimitExceeded"):
        raise FatalConnectorError("yt_auth_failed" if response.status == 401 else "yt_forbidden")
    raise RetryableError("yt_server_error" if response.status >= 500 else "yt_rate_limited")


async def find_live_chat_id(
    client: YouTubeClient,
    *,
    clock: Clock | None = None,
    retry_s: float = 10.0,
    max_attempts: int | None = None,
) -> str | None:
    """Poll for an active broadcast. Returns its liveChatId; ``None`` only when ``max_attempts`` ran out."""
    clk: Clock = clock or SystemClock()
    attempt = 0
    while max_attempts is None or attempt < max_attempts:
        attempt += 1
        try:
            chat_id = await lookup_live_chat_id(client)
        except QuotaDenied:
            await clk.sleep(max(retry_s, 60.0))
            continue
        except RetryableError as exc:
            log_event(_LOG, "lookup_retry", level=logging.WARNING, code=exc.code)
            chat_id = None
        if chat_id:
            return chat_id
        await clk.sleep(retry_s)
    return None
