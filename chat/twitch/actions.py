"""Twitch moderation actions through Helix: delete a message, time out a user.

``DELETE /helix/moderation/chat`` (scope ``moderator:manage:chat_messages``) and
``POST /helix/moderation/bans`` with ``data.duration`` 1..1,209,600 s (scope
``moderator:manage:banned_users``). ``moderator_id`` is the token's user. Helix allows 800 points per
minute (UNVERIFIED); each action costs one point and a token bucket keeps us well below that.
Twitch refuses moderation of the broadcaster and of moderators, so those are never attempted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from chat.common.bucket import TokenBucket
from chat.common.clock import Clock, SystemClock
from chat.common.errors import ChatError
from chat.common.http import FixedOriginClient, HttpResponse
from chat.common.logsafe import hash_id, log_event
from chat.common.results import ActionResult
from contracts import ChatMessage

from .auth import UserSession
from .errors import InvalidToken

_LOG = logging.getLogger("chat.twitch.actions")
MAX_TIMEOUT_S = 1_209_600
BLOCKED_CODE = "twitch_actions_blocked"
_REASON = "stream_sarthi"  # fixed text; never any message content


class TwitchActions:
    def __init__(
        self,
        http: FixedOriginClient,
        session: UserSession,
        broadcaster_id: str,
        *,
        clock: Clock | None = None,
        on_status: Callable[[str], None] | None = None,
        rate_per_s: float = 10.0,
        burst: int = 100,
    ) -> None:
        self._http = http
        self._session = session
        self._broadcaster_id = broadcaster_id
        self._clock: Clock = clock or SystemClock()
        self._on_status = on_status
        self._bucket = TokenBucket(rate_per_s, burst, clock=self._clock.monotonic, sleep=self._clock.sleep)
        self._cooldown_until = 0.0
        self.blocked_reason: str | None = None

    async def delete(self, message: ChatMessage) -> ActionResult:
        early = self._precheck(message)
        if early is not None:
            return early
        params = {"message_id": message.message_id}
        return await self._send(
            "DELETE", "/helix/moderation/chat", message, "delete", params=params, ok=(204, 200)
        )

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult:
        if not 1 <= seconds <= MAX_TIMEOUT_S:
            raise ValueError("seconds must be within 1..1209600")
        early = self._precheck(message)
        if early is not None:
            return early
        body = {"data": {"user_id": message.author_id, "duration": int(seconds), "reason": _REASON}}
        return await self._send("POST", "/helix/moderation/bans", message, "timeout", body=body, ok=(200,))

    # ---- internals ----------------------------------------------------------------------------
    def _precheck(self, message: ChatMessage) -> ActionResult | None:
        if message.platform != "twitch":
            return ActionResult("unsupported", "wrong_platform")
        if message.is_broadcaster or message.is_moderator:
            return ActionResult("unsupported", "privileged_author")
        if self.blocked_reason is not None:
            return ActionResult("failed", "actions_stopped")
        if self._clock.monotonic() < self._cooldown_until:
            return ActionResult("failed", "rate_limited")
        return None

    def _block(self, reason: str) -> None:
        if self.blocked_reason is None:
            self.blocked_reason = reason
            log_event(_LOG, "actions_blocked", level=logging.WARNING, reason=reason)
            if self._on_status is not None:
                self._on_status(BLOCKED_CODE)

    async def _send(
        self,
        method: str,
        path: str,
        message: ChatMessage,
        kind: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
        ok: tuple[int, ...],
    ) -> ActionResult:
        await self._bucket.acquire()
        try:
            token = await self._session.access_token()
            moderator_id = self._session.user_id or ""
            response = await self._http.request(
                method,
                path,
                params={
                    "broadcaster_id": self._broadcaster_id,
                    "moderator_id": moderator_id,
                    **(params or {}),
                },
                headers={
                    "Client-Id": self._session.auth.client_id,
                    "Authorization": "Bearer " + token.reveal(),
                },
                json_body=body,
            )
        except InvalidToken:
            self._block("auth")
            return ActionResult("failed", "auth")
        except ChatError as exc:
            return ActionResult("failed", exc.code)
        return self._result(message, kind, response, ok)

    def _result(
        self, message: ChatMessage, kind: str, response: HttpResponse, ok: tuple[int, ...]
    ) -> ActionResult:
        mid = hash_id(message.message_id)
        status = response.status
        if status in ok:
            log_event(_LOG, "action_done", mid=mid, kind=kind)
            return ActionResult("done", "ok")
        log_event(_LOG, "action_failed", level=logging.WARNING, mid=mid, kind=kind, status=status)
        if status == 401:
            self._block("auth")
            return ActionResult("failed", "auth")
        if status == 403:
            self._block("permissions")  # not a moderator in this channel, or a scope is missing
            return ActionResult("failed", "permissions")
        if status == 429:
            self._cooldown_until = self._clock.monotonic() + self._retry_delay(response)
            return ActionResult("failed", "rate_limited")
        if status == 404:
            return ActionResult("failed", "gone")
        return ActionResult("failed", "server_error" if status >= 500 else "rejected")

    def _retry_delay(self, response: HttpResponse) -> float:
        reset = response.headers.get("ratelimit-reset", "")
        try:
            return min(60.0, max(1.0, float(reset) - self._clock.wall()))
        except ValueError:
            return 10.0
