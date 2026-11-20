"""Platform actions on YouTube: delete a message (20 units) or time out a user (200 units).

Deleting is preferred; a timeout is a ``liveChatBans.insert`` of type ``temporary`` and is off unless
``allow_timeout`` is set. Every action is charged to the quota governor first. When the action
budget is down to its reserve, a 403 quota error arrives, or permissions are missing, actions stop
and the result is ``failed`` so the caller keeps hiding messages on our side only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal

from chat.common.bucket import TokenBucket
from chat.common.clock import Clock, SystemClock
from chat.common.logsafe import hash_id, log_event
from chat.common.results import ActionResult
from contracts import ChatMessage

from .client import QuotaDenied, YouTubeClient
from .http import api_error_reason
from .normalize import IdMap

_LOG = logging.getLogger("chat.youtube.actions")
_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
BLOCKED_CODE = "yt_actions_blocked"


class YouTubeActions:
    def __init__(
        self,
        client: YouTubeClient,
        *,
        get_live_chat_id: Callable[[], str | None],
        ids: IdMap | None = None,
        allow_timeout: bool = False,
        clock: Clock | None = None,
        on_status: Callable[[str], None] | None = None,
        rate_per_s: float = 2.0,
        burst: int = 5,
    ) -> None:
        self._client = client
        self._get_live_chat_id = get_live_chat_id
        self._ids = ids or IdMap()
        self.allow_timeout = allow_timeout
        self._clock: Clock = clock or SystemClock()
        self._on_status = on_status
        self._bucket = TokenBucket(rate_per_s, burst, clock=self._clock.monotonic, sleep=self._clock.sleep)
        self._cooldown_until = 0.0
        self.blocked_reason: str | None = None

    # ---- public -------------------------------------------------------------------------------
    async def delete(self, message: ChatMessage) -> ActionResult:
        early = self._precheck(message, "delete")
        if early is not None:
            return early
        await self._bucket.acquire()
        try:
            response = await self._client.call(
                "DELETE",
                "/youtube/v3/liveChat/messages",
                "delete",
                params={"id": self._ids.resolve(message.message_id)},
            )
        except QuotaDenied:
            return ActionResult("failed", "quota_low")
        return self._result(
            message, "delete", response.status, api_error_reason(response.body), ok=(204, 200)
        )

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult:
        if not self.allow_timeout:
            return ActionResult("unsupported", "timeout_disabled")
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        early = self._precheck(message, "ban")
        if early is not None:
            return early
        chat_id = self._get_live_chat_id()
        if not chat_id:
            return ActionResult("failed", "no_live_chat")
        await self._bucket.acquire()
        body = {
            "snippet": {
                "liveChatId": self._ids.resolve(chat_id),
                "type": "temporary",
                "banDurationSeconds": int(seconds),
                "bannedUserDetails": {"channelId": self._ids.resolve(message.author_id)},
            }
        }
        try:
            response = await self._client.call(
                "POST", "/youtube/v3/liveChat/bans", "ban", params={"part": "snippet"}, json_body=body
            )
        except QuotaDenied:
            return ActionResult("failed", "quota_low")
        return self._result(message, "ban", response.status, api_error_reason(response.body), ok=(200,))

    # ---- internals ----------------------------------------------------------------------------
    def _precheck(self, message: ChatMessage, kind: Literal["delete", "ban"]) -> ActionResult | None:
        if message.platform != "youtube":
            return ActionResult("unsupported", "wrong_platform")
        if message.is_broadcaster or message.is_moderator:
            return ActionResult("unsupported", "privileged_author")
        if self.blocked_reason is not None:
            return ActionResult("failed", "actions_stopped")
        if self._clock.monotonic() < self._cooldown_until:
            return ActionResult("failed", "rate_limited")
        if not self._client.governor.can_afford(kind):
            return ActionResult("failed", "quota_low")
        return None

    def _block(self, reason: str) -> None:
        if self.blocked_reason is None:
            self.blocked_reason = reason
            log_event(_LOG, "actions_blocked", level=logging.WARNING, reason=reason)
            if self._on_status is not None:
                self._on_status(BLOCKED_CODE)

    def _result(
        self, message: ChatMessage, kind: str, status: int, reason: str, *, ok: tuple[int, ...]
    ) -> ActionResult:
        mid = hash_id(message.message_id)
        if status in ok:
            log_event(_LOG, "action_done", mid=mid, kind=kind)
            return ActionResult("done", "ok")
        if status == 403 and reason in _QUOTA_REASONS:
            self._block("quota")
            return ActionResult("failed", "quota")
        if status == 403 and reason == "insufficientPermissions":
            self._block("permissions")
            return ActionResult("failed", "permissions")
        if status in (403, 429) and reason in _RATE_REASONS or status == 429:
            self._cooldown_until = self._clock.monotonic() + 30.0
            return ActionResult("failed", "rate_limited")
        log_event(_LOG, "action_failed", level=logging.WARNING, mid=mid, kind=kind, status=status)
        if status == 404:
            return ActionResult("failed", "gone")
        if status == 401:
            return ActionResult("failed", "auth")
        return ActionResult("failed", "server_error" if status >= 500 else "forbidden")
