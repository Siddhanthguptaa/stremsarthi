"""YouTube OAuth for an installed (desktop) app: loopback redirect + PKCE (RFC 7636 / RFC 8252).

Flow: bind ``127.0.0.1:<random port>``, print the consent URL (the user opens it in the Windows
browser; WSL localhost forwarding delivers the redirect back), accept exactly one callback whose
``state`` matches, then exchange the code with the PKCE verifier. Google's device-code flow does not
cover the ``youtube.force-ssl`` scope, so it is not used. Tokens live in RAM; the refresh token can
optionally be kept in the OS keyring (``YouTubeTokenStore``, opt-in, never a plaintext file).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from aiohttp import web

from chat.common.clock import Clock, SystemClock
from chat.common.errors import SecretError
from chat.common.http import FixedOriginClient
from chat.common.secret import Secret

from .errors import YouTubeError

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALLBACK_PATH = "/callback"
_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Stream Sarthi</title>"
    "<p>Stream Sarthi: you can close this tab and return to the app.</p>"
)


@dataclass(frozen=True, slots=True)
class YouTubeTokens:
    access: Secret
    refresh: Secret | None
    expires_at: float  # monotonic seconds


def make_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using S256."""
    verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43..128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class LoopbackReceiver:
    """One-shot HTTP listener on ``127.0.0.1`` that captures the OAuth redirect."""

    def __init__(self, state: str) -> None:
        self._state = state
        self._runner: web.AppRunner | None = None
        self._future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.port = 0

    async def start(self) -> int:
        app = web.Application()
        app.router.add_get(CALLBACK_PATH, self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]
        return self.port

    async def _handle(self, request: web.Request) -> web.Response:
        # DNS-rebinding guard: only the literal loopback host we advertised is accepted.
        if request.headers.get("Host") != f"127.0.0.1:{self.port}":
            return web.Response(status=400)
        if self._future.done():
            return web.Response(status=409)
        state = request.query.get("state", "")
        if not hmac.compare_digest(state.encode(), self._state.encode()):
            return web.Response(status=400)  # keep waiting: a stray request must not abort the login
        if "error" in request.query:
            self._future.set_exception(YouTubeError("oauth_denied"))
            return web.Response(text=_PAGE, content_type="text/html")
        code = request.query.get("code", "")
        if not code:
            return web.Response(status=400)
        self._future.set_result(code)
        return web.Response(text=_PAGE, content_type="text/html")

    async def wait_code(self, timeout_s: float, cancel: asyncio.Event | None = None) -> str:
        waiters: set[asyncio.Future[Any]] = {self._future}
        cancel_task = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
        if cancel_task is not None:
            waiters.add(cancel_task)
        try:
            done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if cancel_task is not None:
                cancel_task.cancel()
        if self._future in done:
            return self._future.result()
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError
        raise YouTubeError("oauth_timeout")

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if not self._future.done():
            self._future.cancel()


class YouTubeAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: Secret | None,
        http: FixedOriginClient,
        *,
        clock: Clock | None = None,
        auth_url: str = AUTH_URL,
    ) -> None:
        if not client_id:
            raise YouTubeError("missing_client_id")
        self.client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._clock: Clock = clock or SystemClock()
        self._auth_url = auth_url

    def build_auth_url(self, redirect_uri: str, challenge: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        return f"{self._auth_url}?{query}"

    async def login(
        self,
        present: Callable[[str], None],
        *,
        timeout_s: float = 300.0,
        cancel: asyncio.Event | None = None,
    ) -> YouTubeTokens:
        """Run the loopback flow. ``present(url)`` shows the consent URL to the owner (no ``/dev/tty``)."""
        verifier, challenge = make_pkce()
        state = secrets.token_urlsafe(24)
        receiver = LoopbackReceiver(state)
        port = await receiver.start()
        try:
            redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
            present(self.build_auth_url(redirect_uri, challenge, state))
            code = await receiver.wait_code(timeout_s, cancel)
        finally:
            await receiver.close()
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            previous_refresh=None,
        )

    async def refresh(self, tokens: YouTubeTokens) -> YouTubeTokens:
        if tokens.refresh is None:
            raise YouTubeError("yt_auth_failed")
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": tokens.refresh.reveal()},
            previous_refresh=tokens.refresh,
        )

    async def _token_request(self, fields: dict[str, str], previous_refresh: Secret | None) -> YouTubeTokens:
        data = {"client_id": self.client_id, **fields}
        if self._client_secret is not None:
            data["client_secret"] = self._client_secret.reveal()
        response = await self._http.request("POST", "/token", data=data)
        body = response.body
        if response.status != 200:
            error = body.get("error")
            if error == "invalid_grant":
                raise YouTubeError("yt_auth_failed")  # revoked or expired refresh token / used code
            raise YouTubeError("oauth_exchange_failed")
        scopes = body.get("scope")
        token_type = body.get("token_type")
        lifetime = body.get("expires_in")
        if (
            not isinstance(scopes, str)
            or SCOPE not in scopes.split()
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or isinstance(lifetime, bool)
            or not isinstance(lifetime, int | float)
            or lifetime <= 0
        ):
            raise YouTubeError("oauth_invalid_response")
        try:
            access = Secret(body.get("access_token"))  # type: ignore[arg-type]
            refresh_raw = body.get("refresh_token")
            refresh = Secret(refresh_raw) if refresh_raw else previous_refresh
        except SecretError:
            raise YouTubeError("oauth_invalid_response") from None
        return YouTubeTokens(access, refresh, self._clock.monotonic() + float(lifetime))


class YouTubeSession:
    """Hands out a valid access token, refreshing under one lock (refresh calls never overlap)."""

    def __init__(self, auth: YouTubeAuth, tokens: YouTubeTokens, *, clock: Clock | None = None) -> None:
        self._auth = auth
        self._tokens = tokens
        self._clock: Clock = clock or SystemClock()
        self._lock = asyncio.Lock()

    async def access_token(self, *, force_refresh: bool = False) -> Secret:
        async with self._lock:
            if force_refresh or self._clock.monotonic() >= self._tokens.expires_at - 60:
                self._tokens = await self._auth.refresh(self._tokens)
            return self._tokens.access


class YouTubeTokenStore:
    """Opt-in OS keyring storage of the refresh token only. Never a plaintext fallback.

    Adapted from reference/irl-shield/shield/twitch/storage.py (MIT, Copyright (c) 2026 Elan Nash).
    """

    _ALLOWED = {
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
    }

    def __init__(self, backend: Any = None) -> None:
        if backend is None:
            import keyring

            backend = keyring.get_keyring()
            if type(backend).__module__ + "." + type(backend).__name__ not in self._ALLOWED:
                raise YouTubeError("keyring_unavailable")
        self._backend = backend

    def save(self, client_id: str, tokens: YouTubeTokens, *, remember: bool = False) -> None:
        if remember is not True or tokens.refresh is None:
            return
        try:
            self._backend.set_password(
                "stream-sarthi-youtube:" + client_id,
                "default",
                json.dumps({"refresh": tokens.refresh.reveal()}),
            )
        except Exception:
            raise YouTubeError("keyring_write_failed") from None

    def load(self, client_id: str) -> YouTubeTokens | None:
        try:
            raw = self._backend.get_password("stream-sarthi-youtube:" + client_id, "default")
            if raw is None:
                return None
            # expires_at 0: the monotonic deadline cannot survive a process, so refresh on first use
            return YouTubeTokens(Secret("unset"), Secret(json.loads(raw)["refresh"]), 0.0)
        except Exception:
            raise YouTubeError("keyring_read_failed") from None
