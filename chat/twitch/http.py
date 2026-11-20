"""Allowlisted Twitch routes on the shared fixed-origin client (no redirects, 64 KiB cap)."""

from __future__ import annotations

import aiohttp

from chat.common.http import FixedOriginClient

ORIGINS = {"id": "https://id.twitch.tv", "api": "https://api.twitch.tv"}
ROUTES = {
    ("POST", "/oauth2/device"): "id",
    ("POST", "/oauth2/token"): "id",
    ("GET", "/oauth2/validate"): "id",
    ("POST", "/helix/eventsub/subscriptions"): "api",
    ("DELETE", "/helix/moderation/chat"): "api",
    ("POST", "/helix/moderation/bans"): "api",
    ("GET", "/helix/users"): "api",
}


def make_http(session: aiohttp.ClientSession, *, loopback_origin: str | None = None) -> FixedOriginClient:
    return FixedOriginClient(session, origins=ORIGINS, routes=ROUTES, loopback_origin=loopback_origin)
