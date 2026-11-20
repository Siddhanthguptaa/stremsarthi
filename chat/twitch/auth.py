"""Twitch device-code grant (public client), hourly validation and RAM-only refresh rotation.

Adapted from reference/irl-shield/shield/twitch/auth.py (MIT, Copyright (c) 2026 Elan Nash; licence in
``LICENSE.irl-shield``). Changes: scopes are now ``user:read:chat``, ``moderator:manage:chat_messages``
and ``moderator:manage:banned_users`` (IRL Shield's shield-mode and stream-key scopes are dropped);
``Secret`` moved to ``chat.common``; errors carry fixed codes; the HTTP layer returns ``HttpResponse``;
the session can learn the token's user id from the first validation instead of requiring it up front.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from chat.common.errors import SecretError
from chat.common.http import FixedOriginClient, HttpResponse
from chat.common.secret import Secret

from .errors import InvalidToken, TwitchError

SCOPES = ("user:read:chat", "moderator:manage:chat_messages", "moderator:manage:banned_users")
_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@dataclass(frozen=True, slots=True)
class DevicePrompt:
    uri: Secret
    code: Secret


@dataclass(frozen=True, slots=True)
class Tokens:
    access: Secret
    refresh: Secret
    expires_at: float


def positive(value: object) -> float:
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, int | float)
            and math.isfinite(value)
            and value > 0
        )
    except OverflowError:
        valid = False
    if not valid:
        raise TwitchError("twitch_invalid_lifetime")
    return float(value)  # type: ignore[arg-type]


def approved_scopes(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == len(SCOPES)
        and all(isinstance(scope, str) for scope in value)
        and set(value) == set(SCOPES)
    )


async def cancellable_wait(seconds: float, cancel: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(cancel.wait(), seconds)
    except TimeoutError:
        return
    raise asyncio.CancelledError


class DeviceAuth:
    def __init__(
        self,
        client_id: str,
        http: FixedOriginClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float, asyncio.Event], Awaitable[None]] = cancellable_wait,
    ) -> None:
        self.client_id, self.http = client_id, http
        self.clock, self.wait = clock, wait

    def _tokens(self, body: dict[str, Any]) -> Tokens:
        token_type = body.get("token_type")
        if (
            not approved_scopes(body.get("scope"))
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
        ):
            raise TwitchError("twitch_scope_mismatch")
        try:
            return Tokens(
                Secret(body.get("access_token")),  # type: ignore[arg-type]
                Secret(body.get("refresh_token")),  # type: ignore[arg-type]
                self.clock() + positive(body.get("expires_in")),
            )
        except SecretError:
            raise TwitchError("twitch_invalid_token_response") from None

    async def _request(
        self,
        cancel: asyncio.Event,
        method: str,
        path: str,
        *,
        deadline: float | None = None,
        data: dict[str, str] | None = None,
    ) -> HttpResponse:
        if cancel.is_set():
            raise asyncio.CancelledError
        if deadline is not None and self.clock() >= deadline:
            raise TwitchError("device_expired")
        request = asyncio.create_task(self.http.request(method, path, data=data))
        cancelled = asyncio.create_task(cancel.wait())
        try:
            timeout = None if deadline is None else max(0.0, deadline - self.clock())
            done, _ = await asyncio.wait(
                {request, cancelled}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel.is_set():
                raise asyncio.CancelledError
            if request not in done or (deadline is not None and self.clock() >= deadline):
                raise TwitchError("device_expired")
            return request.result()
        finally:
            # Both event cancellation and caller-task cancellation own the HTTP task.
            request.cancel()
            cancelled.cancel()
            await asyncio.gather(request, cancelled, return_exceptions=True)

    async def login(self, present: Callable[[DevicePrompt], None], cancel: asyncio.Event) -> Tokens:
        """Run the device-code flow. ``present(prompt)`` shows the URL and code to the owner."""
        if cancel.is_set():
            raise asyncio.CancelledError
        began = self.clock()
        scopes = " ".join(SCOPES)
        response = await self._request(
            cancel, "POST", "/oauth2/device", data={"client_id": self.client_id, "scopes": scopes}
        )
        body = response.body
        if response.status != 200:
            raise TwitchError("device_unavailable")
        try:
            device = Secret(body.get("device_code"))  # type: ignore[arg-type]
            prompt = DevicePrompt(
                Secret(body.get("verification_uri")),  # type: ignore[arg-type]
                Secret(body.get("user_code")),  # type: ignore[arg-type]
            )
        except SecretError:
            raise TwitchError("device_invalid_response") from None
        deadline = began + positive(body.get("expires_in"))
        interval = positive(body.get("interval", 5))
        # Validate before handing a secret-bearing URI to the owner presenter.
        try:
            uri = urlsplit(prompt.uri.reveal())
        except ValueError:
            raise TwitchError("device_invalid_endpoint") from None
        if uri.scheme != "https" or uri.netloc != "www.twitch.tv" or uri.path != "/activate" or uri.fragment:
            raise TwitchError("device_invalid_endpoint")
        present(prompt)
        while True:
            if cancel.is_set():
                raise asyncio.CancelledError
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise TwitchError("device_expired")
            await self.wait(min(interval, remaining), cancel)
            if cancel.is_set():
                raise asyncio.CancelledError
            if self.clock() >= deadline:
                raise TwitchError("device_expired")
            response = await self._request(
                cancel,
                "POST",
                "/oauth2/token",
                deadline=deadline,
                data={
                    "client_id": self.client_id,
                    "scopes": scopes,
                    "device_code": device.reveal(),
                    "grant_type": _GRANT,
                },
            )
            if self.clock() >= deadline:
                raise TwitchError("device_expired")
            if response.status == 200:
                return self._tokens(response.body)
            # Twitch uses ``message``; RFC 8628 uses ``error``. Never propagate other text.
            error = response.body.get("error", response.body.get("message"))
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error in ("expired_token", "invalid device code"):
                raise TwitchError("device_expired")
            if error == "access_denied":
                raise TwitchError("device_denied")
            raise TwitchError("device_failed")

    async def refresh(self, tokens: Tokens) -> Tokens:
        response = await self.http.request(
            "POST",
            "/oauth2/token",
            data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh.reveal(),
            },
        )
        if response.status != 200:
            raise TwitchError("twitch_refresh_failed")
        return self._tokens(response.body)


class UserSession:
    """Validate at startup and hourly; rotate public-client refresh tokens in RAM.

    ``expected_user_id`` pins the account when known; otherwise the first validation learns it
    (``user_id``) and every later validation must match it.
    """

    def __init__(self, auth: DeviceAuth, tokens: Tokens, expected_user_id: str | None = None) -> None:
        self.auth, self.tokens = auth, tokens
        self.user_id = expected_user_id
        self._validated_at: float | None = None
        self._lock = asyncio.Lock()

    async def access_token(self) -> Secret:
        # Public-client refresh tokens are single-use; callers share one rotation.
        async with self._lock:
            return await self._access_token()

    async def _access_token(self) -> Secret:
        if self.auth.clock() >= self.tokens.expires_at - 60:
            self.tokens = await self.auth.refresh(self.tokens)
            self._validated_at = None
        now = self.auth.clock()
        if self._validated_at is None or now - self._validated_at >= 3600:
            response = await self.auth.http.request(
                "GET", "/oauth2/validate", headers={"Authorization": "OAuth " + self.tokens.access.reveal()}
            )
            if response.status == 401:
                raise InvalidToken
            body = response.body
            user_id = body.get("user_id")
            if (
                response.status != 200
                or body.get("client_id") != self.auth.client_id
                or not isinstance(user_id, str)
                or not user_id
                or (self.user_id is not None and user_id != self.user_id)
                or not approved_scopes(body.get("scopes"))
            ):
                raise TwitchError("twitch_validation_failed")
            try:
                expires_at = now + positive(body.get("expires_in"))
            except TwitchError:
                raise TwitchError("twitch_validation_failed") from None
            self.user_id = user_id
            self.tokens = Tokens(
                self.tokens.access, self.tokens.refresh, min(self.tokens.expires_at, expires_at)
            )
            self._validated_at = now
        return self.tokens.access
