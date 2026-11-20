"""Signed session tokens (HMAC-SHA256, stdlib only).

Adapted from Saximn/privastream ``src/web/backend/auth.py`` (Apache-2.0, see LICENSE.privastream).
Changes: the signing key is passed in (``secret``) instead of read from the environment (so it
can come from ``.env`` through the settings loader), ``REQUIRE_AUTH`` defaults to true (see
``settings.py``), and a thin ``TokenService`` adds the Stream Sarthi roles (``streamer``,
``moderator``, ``hotkey``, ``viewer-readonly``). The token format and the signing and
verification logic are unchanged: ``base64url(payload).base64url(sig)``.

``room_id`` is the session scope of the token; roles are validated by ``TokenService``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

ROLES = ("streamer", "moderator", "hotkey", "viewer-readonly")
HOTKEY_ROOM = "hotkey"  # fixed scope of hotkey tokens, see TokenService.verify
ROLE_TTL_S = {"streamer": 6 * 3600, "moderator": 6 * 3600, "hotkey": 24 * 3600, "viewer-readonly": 6 * 3600}


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(secret: str | bytes) -> bytes:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise RuntimeError("SECRET_KEY must be set to sign/verify tokens")
    return key


def issue_room_token(
    secret: str | bytes, room_id: str, role: str = "viewer", ttl_seconds: int = 6 * 3600
) -> str:
    """Mint a signed token binding a client to a room and role."""
    payload = {
        "room_id": room_id,
        "role": role,
        "exp": int(time.time()) + ttl_seconds,
    }
    body = _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64u_encode(hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_room_token(secret: str | bytes, token: str, room_id: str | None = None) -> dict[str, Any] | None:
    """Return the payload dict if valid (and matches room_id), else None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64u_encode(hmac.new(_key(secret), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64u_decode(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        if room_id is not None and payload.get("room_id") != room_id:
            return None
        return payload
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling. ``actor`` is an opaque hash (never the token) for ``HoldCommand.actor``."""

    role: str
    room_id: str
    exp: int
    actor: str


class TokenService:
    """Issues and verifies role tokens for one API instance (``room_id`` = the session scope)."""

    def __init__(self, secret: str, room_id: str) -> None:
        self._secret = _key(secret)
        self.room_id = room_id

    def issue(self, role: str, ttl_seconds: int | None = None) -> str:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        room = HOTKEY_ROOM if role == "hotkey" else self.room_id
        return issue_room_token(self._secret, room, role, ttl_seconds or ROLE_TTL_S[role])

    def verify(self, token: str | None) -> Principal | None:
        if not token:
            return None
        payload = verify_room_token(self._secret, token)  # signature and expiry
        if payload is None or payload.get("role") not in ROLES:
            return None
        room = payload.get("room_id")
        # A hotkey token (24 h, written to a file by the Windows helper) must survive an API
        # restart, which creates a new random room id. It is bound to SECRET_KEY and can only
        # hold or release, so a fixed scope is safe. Every other role is bound to this instance.
        if not (room == self.room_id or (payload["role"] == "hotkey" and room == HOTKEY_ROOM)):
            return None
        # actor: stable per token, reveals nothing about it (HMAC of the signature part).
        actor = hmac.new(self._secret, token.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        return Principal(role=payload["role"], room_id=self.room_id, exp=int(payload["exp"]), actor=actor)
