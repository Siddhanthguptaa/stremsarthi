"""Fixed-origin HTTP client: allowlisted routes only, no redirects, bounded bodies, code-only errors.

Adapted from reference/irl-shield/shield/twitch/http.py (MIT, Copyright (c) 2026 Elan Nash). The
Twitch-specific allowlist moved to ``chat/twitch/http.py``; routes and origins are now parameters so
YouTube uses the same client. Errors never carry a URL, response body or header value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .errors import ChatError

_KEPT_HEADERS = ("retry-after", "ratelimit-reset", "ratelimit-remaining")


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: dict[str, Any] = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


def validate_loopback_origin(origin: str) -> str:
    """Accept only ``http://127.0.0.1:<port>`` (used to point the clients at an in-process fake)."""
    try:
        parsed = urlsplit(origin)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and 0 < parsed.port <= 65535
            and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
            and not any(ord(char) <= 32 for char in origin)
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("loopback_origin must look like http://127.0.0.1:<port>")
    return origin


class FixedOriginClient:
    """``routes`` maps ``(METHOD, path)`` to an origin key in ``origins``.

    Anything not listed raises ``ChatError("http_unapproved")`` before a socket is opened.
    ``loopback_origin`` redirects every route to a local fake (tests, offline demos).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        origins: Mapping[str, str],
        routes: Mapping[tuple[str, str], str],
        loopback_origin: str | None = None,
        max_bytes: int = 65_536,
        timeout_s: float = 20.0,
    ) -> None:
        self.session = session
        self._origins = dict(origins)
        self._routes = dict(routes)
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s
        self.loopback_origin = validate_loopback_origin(loopback_origin) if loopback_origin else None
        for origin_key in self._routes.values():
            if origin_key not in self._origins:
                raise ValueError("route references an unknown origin")

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        data: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        route = (method.upper(), path)
        origin_key = self._routes.get(route)
        if origin_key is None:
            raise ChatError("http_unapproved")
        origin = self.loopback_origin or self._origins[origin_key]
        try:
            async with self.session.request(
                route[0],
                origin + path,
                params=params,
                headers=headers,
                json=json_body,
                data=data,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=self._timeout_s),
            ) as response:
                if response.status // 100 == 3:
                    raise ChatError("http_redirect")
                if response.content_length and response.content_length > self._max_bytes:
                    raise ChatError("http_oversized")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > self._max_bytes:
                        raise ChatError("http_oversized")
                kept = {k: response.headers[k] for k in _KEPT_HEADERS if k in response.headers}
                return HttpResponse(response.status, self._parse(response.status, bytes(raw)), kept)
        except ChatError:
            raise
        except (aiohttp.ClientError, TimeoutError, RecursionError):
            raise ChatError("http_failed") from None

    @staticmethod
    def _parse(status: int, raw: bytes) -> dict[str, Any]:
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            if status >= 400:
                return {}  # an error status is definitive even when its body is not JSON
            raise ChatError("http_invalid_body") from None
        if not isinstance(body, dict):
            if status >= 400:
                return {}
            raise ChatError("http_invalid_body")
        return body


__all__ = ["FixedOriginClient", "HttpResponse", "validate_loopback_origin"]
