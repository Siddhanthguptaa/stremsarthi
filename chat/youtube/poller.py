"""``liveChatMessages.list`` poller.

Honours ``pollingIntervalMillis`` and ``nextPageToken``, spaces calls with the quota governor
(``max(server interval, governor interval, min_poll_s)``), suppresses duplicates, skips the stale
backlog on the first page and maps platform failures to retry / finish / fatal:

* 5xx, 429, ``rateLimitExceeded``  -> retry with backoff (the page token is kept)
* ``quotaExceeded``                -> reads pause until the quota resets at midnight Pacific
* ``liveChatEnded`` / 404          -> the connector finishes (the stream's chat closed)
* 401 after a forced refresh, other 403 -> fatal
"""

from __future__ import annotations

import inspect
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from chat.common.connector import Connector, FatalConnectorError, FinishedError, RetryableError
from chat.common.logsafe import hash_id, log_event
from contracts import ChatMessage

from .broadcast import find_live_chat_id
from .client import QuotaDenied, YouTubeClient
from .http import api_error_reason
from .normalize import CHAT_ENDED_TYPE, IdMap, item_type, normalize_item

OnMessage = Callable[[ChatMessage], Awaitable[None] | None]
_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_ENDED_REASONS = frozenset({"liveChatEnded", "liveChatDisabled", "liveChatNotFound"})


class YouTubePoller(Connector):
    def __init__(
        self,
        client: YouTubeClient,
        on_message: OnMessage,
        *,
        live_chat_id: str | None = None,
        ids: IdMap | None = None,
        min_poll_s: float = 2.0,
        backlog_max_age_s: float = 120.0,
        seen_capacity: int = 5000,
        **connector_kwargs: Any,
    ) -> None:
        super().__init__("youtube", **connector_kwargs)
        self.client = client
        self.on_message = on_message
        self.live_chat_id = live_chat_id
        self.ids = ids or IdMap()
        self.min_poll_s = min_poll_s
        self.backlog_max_age_s = backlog_max_age_s
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._seen_capacity = seen_capacity
        self._page_token: str | None = None
        self._first_page = True
        self.polls = 0
        self.delivered = 0
        self.skipped_backlog = 0

    # ---- session ------------------------------------------------------------------------------
    async def _run_session(self) -> None:
        gov = self.client.governor
        if self.live_chat_id is None:
            self.live_chat_id = await find_live_chat_id(self.client, clock=self.clock)
        gov.start_stream()
        while not self.stopped:
            if not gov.reads_enabled:
                # budget spent: wait for the Pacific-midnight reset in bounded slices so stop() stays prompt
                await self.clock.sleep(min(gov.seconds_until_reset() + 1.0, 300.0))
                continue
            delay = await self._poll_once()
            await self.clock.sleep(delay)

    async def _poll_once(self) -> float:
        params: dict[str, str | int] = {
            "liveChatId": self.live_chat_id or "",
            "part": "snippet,authorDetails",
            "maxResults": 200,
        }
        if self._page_token:
            params["pageToken"] = self._page_token
        try:
            response = await self.client.call("GET", "/youtube/v3/liveChat/messages", "list", params=params)
        except QuotaDenied:
            return self.min_poll_s
        self.polls += 1
        status = response.status
        if status == 200:
            return await self._handle_page(response.body)
        reason = api_error_reason(response.body)
        if status == 403 and reason in _QUOTA_REASONS:
            return self.min_poll_s  # governor is marked exhausted; the session loop waits for the reset
        if reason in _ENDED_REASONS or status == 404:
            raise FinishedError("yt_chat_ended")
        if status == 403 and reason in _RATE_REASONS or status in (429, 500, 502, 503, 504):
            raise RetryableError("yt_rate_limited" if status in (403, 429) else "yt_server_error")
        if status == 400:
            self._page_token = None  # stale or invalid page token: restart from the live edge
            return self.min_poll_s
        if status == 401:
            raise FatalConnectorError("yt_auth_failed")
        if status == 403:
            raise FatalConnectorError("yt_forbidden")
        raise RetryableError("yt_unexpected_status")

    async def _handle_page(self, body: dict[str, Any]) -> float:
        items = body.get("items")
        ended = False
        now = self.clock.wall()
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if item_type(item) == CHAT_ENDED_TYPE:
                ended = True
                continue
            raw_id = item.get("id")
            if not isinstance(raw_id, str) or not raw_id or raw_id in self._seen:
                continue
            self._remember(raw_id)
            message = normalize_item(
                item, live_chat_id=self.live_chat_id or "", received_at=now, ids=self.ids
            )
            if message is None:
                continue
            if self._first_page and self._is_backlog(item, now):
                self.skipped_backlog += 1
                continue
            await self._deliver(message)
        token = body.get("nextPageToken")
        if isinstance(token, str) and token:
            self._page_token = token
        self._first_page = False
        self.mark_healthy()
        if ended:
            raise FinishedError("yt_chat_ended")
        server_ms = body.get("pollingIntervalMillis")
        server_s = float(server_ms) / 1000.0 if isinstance(server_ms, int | float) and server_ms > 0 else 0.0
        # read_interval() is inf once the read budget is spent; the session loop then waits for the
        # reset, so never sleep "forever" here (bounded, and a clock cannot advance by infinity).
        return min(300.0, max(server_s, self.client.governor.read_interval(), self.min_poll_s))

    def _remember(self, raw_id: str) -> None:
        self._seen[raw_id] = None
        while len(self._seen) > self._seen_capacity:
            self._seen.popitem(last=False)

    def _is_backlog(self, item: dict[str, Any], now: float) -> bool:
        snippet = item.get("snippet")
        published = snippet.get("publishedAt") if isinstance(snippet, dict) else None
        if not isinstance(published, str):
            return False
        try:
            at = datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False
        return now - at > self.backlog_max_age_s

    async def _deliver(self, message: ChatMessage) -> None:
        try:
            result = self.on_message(message)
            if inspect.isawaitable(result):
                await result
            self.delivered += 1
        except Exception as exc:  # a bad consumer must not stop polling; never log the message
            log_event(
                self._logger,
                "deliver_failed",
                level=logging.WARNING,
                mid=hash_id(message.message_id),
                error=type(exc).__name__,
            )
