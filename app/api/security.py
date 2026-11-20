"""Host/Origin checks, security headers, CSP and body limits (pure ASGI, WebSocket aware).

Pattern adapted from IRL Shield's dashboard server (MIT): exact Host and Origin matching, no
wildcard CORS, no caching. Written as ASGI middleware (not ``BaseHTTPMiddleware``) so it also
guards the WebSocket upgrade and never buffers bodies.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi.responses import JSONResponse

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": CSP,
}
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
GENERAL_BODY_LIMIT = 2 * 1024 * 1024
ENROL_BODY_LIMIT = 12 * 1024 * 1024
ENROL_PREFIX = "/api/enrol"


def hostname_of(host_header: str) -> str:
    """``127.0.0.1:8000`` -> ``127.0.0.1``; ``[::1]:8000`` -> ``[::1]``; case-folded."""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def body_limit_for(path: str) -> int:
    return (
        ENROL_BODY_LIMIT
        if path == ENROL_PREFIX or path.startswith(ENROL_PREFIX + "/")
        else GENERAL_BODY_LIMIT
    )


class _BodyTooLarge(Exception):
    pass


class SecurityMiddleware:
    def __init__(
        self, app: Callable[..., Awaitable[None]], allowed_hosts: list[str], allowed_origins: list[str]
    ) -> None:
        self.app = app
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = hostname_of(headers.get("host", ""))
        if host not in self.allowed_hosts:
            await self._reject(scope, receive, send, 421, "bad_host", "Host header not allowed")
            return

        origin = headers.get("origin")
        if kind == "websocket":
            if origin is None or origin not in self.allowed_origins:
                await self._reject(scope, receive, send, 403, "bad_origin", "Origin not allowed")
                return
            await self.app(scope, receive, send)
            return

        # REST: a browser always sends Origin on cross-origin and on body-carrying requests, so
        # an Origin that is present must match exactly. Non-browser clients (the hotkey helper,
        # curl) send none and are protected by the bearer token instead.
        if scope["method"] in BODY_METHODS and origin is not None and origin not in self.allowed_origins:
            await self._reject(scope, receive, send, 403, "bad_origin", "Origin not allowed")
            return

        limit = body_limit_for(scope["path"])
        length = headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                await self._reject(scope, receive, send, 400, "bad_request", "invalid Content-Length")
                return
            if int(length) > limit:
                await self._reject(scope, receive, send, 413, "payload_too_large", f"limit is {limit} bytes")
                return

        started = False
        seen = 0

        async def counting_receive() -> MutableMapping[str, Any]:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _BodyTooLarge
            return message

        async def secured_send(message: MutableMapping[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                raw = list(message.get("headers", []))
                present = {k.lower() for k, _ in raw}
                for name, value in SECURITY_HEADERS.items():
                    if name.encode() not in present:
                        raw.append((name.encode(), value.encode()))
                message = {**message, "headers": raw}
            await send(message)

        try:
            await self.app(scope, counting_receive, secured_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(scope, receive, send, 413, "payload_too_large", f"limit is {limit} bytes")

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status: int, code: str, detail: str
    ) -> None:
        if scope["type"] == "websocket":
            # Closing before accept makes the server answer the upgrade with HTTP 403.
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            {"error": code, "detail": detail}, status_code=status, headers=SECURITY_HEADERS
        )
        await response(scope, receive, send)
