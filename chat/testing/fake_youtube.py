"""Fake YouTube Data API + Google OAuth server (loopback only). Synthetic data only.

Mirrors the response shapes of ``liveChatMessages.list``, ``liveBroadcasts.list``,
``liveChatMessages.delete``, ``liveChatBans.insert`` and the OAuth token endpoint, enforces the real
10,000-unit daily quota (so a governor bug shows up as a 403 here) and lets tests inject failures.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from aiohttp import web

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
COSTS = {"list": 5, "lookup": 1, "delete": 20, "ban": 200}


@dataclass
class Injected:
    status: int
    reason: str | None
    times: int
    path: str


@dataclass
class RecordedRequest:
    method: str
    path: str
    params: dict[str, str]
    cost: int
    at: float = 0.0  # ``FakeYouTube.now()`` when received (tests set it to the virtual wall clock)


@dataclass
class FakeYouTube:
    daily_quota: int = 10_000
    polling_interval_ms: int = 5000
    live_chat_id: str = "LIVECHAT_SPECIMEN_1"
    client_id: str = "specimen-client-id"
    token_lifetime_s: int = 3600
    units_used: int = 0
    quota_denied: int = 0
    broadcast_active: bool = True
    chat_ended: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    bans: list[dict[str, Any]] = field(default_factory=list)
    requests: list[RecordedRequest] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    valid_tokens: set[str] = field(default_factory=set)
    refresh_tokens: set[str] = field(default_factory=set)
    _codes: dict[str, tuple[str, str]] = field(default_factory=dict)
    _injected: deque[Injected] = field(default_factory=deque)
    _runner: web.AppRunner | None = None
    origin: str = ""
    now: Callable[[], float] | None = None
    _seq: int = 0

    # ---- lifecycle ----------------------------------------------------------------------------
    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/o/oauth2/v2/auth", self._authorize)
        app.router.add_post("/token", self._token)
        app.router.add_get("/youtube/v3/liveBroadcasts", self._broadcasts)
        app.router.add_get("/youtube/v3/liveChat/messages", self._list)
        app.router.add_delete("/youtube/v3/liveChat/messages", self._delete)
        app.router.add_post("/youtube/v3/liveChat/bans", self._ban)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.origin = f"http://127.0.0.1:{self._runner.addresses[0][1]}"
        return self.origin

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    @property
    def auth_url(self) -> str:
        return self.origin + "/o/oauth2/v2/auth"

    # ---- scripting API for tests -------------------------------------------------------------
    def add_text(
        self,
        text: str,
        *,
        author: str = "UCviewer0000000000000001",
        display: str = "viewer",
        moderator: bool = False,
        owner: bool = False,
        published_at: str = "2026-11-20T10:00:00Z",
        message_id: str | None = None,
        kind: str = "textMessageEvent",
    ) -> str:
        self._seq += 1
        mid = message_id or f"LCC.SPECIMEN.{self._seq:06d}"
        snippet: dict[str, Any] = {
            "type": kind,
            "liveChatId": self.live_chat_id,
            "authorChannelId": author,
            "publishedAt": published_at,
            "hasDisplayContent": True,
            "displayMessage": text,
        }
        if kind == "textMessageEvent":
            snippet["textMessageDetails"] = {"messageText": text}
        elif kind == "superChatEvent":
            snippet["superChatDetails"] = {"amountMicros": "50000000", "currency": "INR", "userComment": text}
        self.items.append(
            {
                "kind": "youtube#liveChatMessage",
                "id": mid,
                "snippet": snippet,
                "authorDetails": {
                    "channelId": author,
                    "displayName": display,
                    "isChatOwner": owner,
                    "isChatModerator": moderator,
                    "isChatSponsor": False,
                },
            }
        )
        return mid

    def add_event(self, kind: str) -> str:
        """A non-text event such as ``messageDeletedEvent`` or ``chatEndedEvent``."""
        self._seq += 1
        mid = f"LCC.EVENT.{self._seq:06d}"
        self.items.append(
            {
                "kind": "youtube#liveChatMessage",
                "id": mid,
                "snippet": {
                    "type": kind,
                    "liveChatId": self.live_chat_id,
                    "publishedAt": "2026-11-20T10:00:00Z",
                },
                "authorDetails": {"channelId": "UCsystem", "displayName": "system"},
            }
        )
        return mid

    def inject(self, status: int, *, reason: str | None = None, times: int = 1, path: str = "") -> None:
        """Fail the next ``times`` API calls whose path starts with ``path`` (default: any API call)."""
        self._injected.append(Injected(status, reason, times, path))

    def expire_tokens(self) -> None:
        self.valid_tokens.clear()

    def calls(self, method: str, path: str) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method == method and r.path == path]

    # ---- OAuth --------------------------------------------------------------------------------
    async def _authorize(self, request: web.Request) -> web.Response:
        q = request.query
        if (
            q.get("client_id") != self.client_id
            or q.get("response_type") != "code"
            or q.get("code_challenge_method") != "S256"
            or SCOPE not in q.get("scope", "").split()
            or not q.get("code_challenge")
        ):
            return web.Response(status=400)
        code = secrets.token_urlsafe(16)
        self._codes[code] = (q["code_challenge"], q.get("redirect_uri", ""))
        location = q["redirect_uri"] + "?" + urlencode({"code": code, "state": q.get("state", "")})
        return web.Response(status=302, headers={"Location": location})

    async def _token(self, request: web.Request) -> web.Response:
        form = {k: str(v) for k, v in (await request.post()).items()}
        self.token_requests.append({k: v for k, v in form.items() if k != "client_secret"})
        if form.get("client_id") != self.client_id:
            return web.json_response({"error": "invalid_client"}, status=401)
        grant = form.get("grant_type")
        body: dict[str, Any] = {
            "expires_in": self.token_lifetime_s,
            "scope": SCOPE,
            "token_type": "Bearer",
        }
        if grant == "authorization_code":
            entry = self._codes.pop(form.get("code", ""), None)
            if entry is None:
                return web.json_response({"error": "invalid_grant"}, status=400)
            challenge, redirect_uri = entry
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if computed != challenge or form.get("redirect_uri") != redirect_uri:
                return web.json_response({"error": "invalid_grant"}, status=400)
            refresh = "refresh-" + secrets.token_hex(8)
            self.refresh_tokens.add(refresh)
            body["refresh_token"] = refresh
        elif grant == "refresh_token":
            if form.get("refresh_token") not in self.refresh_tokens:
                return web.json_response({"error": "invalid_grant"}, status=400)
        else:
            return web.json_response({"error": "unsupported_grant_type"}, status=400)
        access = "access-" + secrets.token_hex(8)
        self.valid_tokens.add(access)
        body["access_token"] = access
        return web.json_response(body)

    # ---- API ----------------------------------------------------------------------------------
    def _gate(self, request: web.Request, kind: str) -> web.Response | None:
        """Auth, failure injection and the real quota. Returns an error response or None."""
        params = dict(request.query)
        cost = COSTS[kind]
        stamp = self.now() if self.now is not None else 0.0
        self.requests.append(RecordedRequest(request.method, request.path, params, cost, stamp))
        bearer = request.headers.get("Authorization", "")
        if not bearer.startswith("Bearer ") or bearer[7:] not in self.valid_tokens:
            return self._error(401, "authError")
        for injected in self._injected:
            if injected.times > 0 and request.path.startswith(injected.path):
                injected.times -= 1
                if injected.times == 0:
                    self._injected.remove(injected)
                self.units_used += cost
                return self._error(injected.status, injected.reason)
        if self.units_used + cost > self.daily_quota:
            self.quota_denied += 1
            return self._error(403, "quotaExceeded")
        self.units_used += cost
        return None

    @staticmethod
    def _error(status: int, reason: str | None) -> web.Response:
        errors = [{"reason": reason, "domain": "youtube"}] if reason else []
        return web.json_response(
            {"error": {"code": status, "message": "specimen error", "errors": errors}}, status=status
        )

    async def _broadcasts(self, request: web.Request) -> web.Response:
        if (denied := self._gate(request, "lookup")) is not None:
            return denied
        filters = [k for k in ("mine", "id", "broadcastStatus") if k in request.query]
        if len(filters) != 1:  # the real API wants exactly one filter
            return self._error(400, "incompatibleParameters")
        items = []
        if self.broadcast_active:
            items = [
                {"kind": "youtube#liveBroadcast", "id": "bc1", "snippet": {"liveChatId": self.live_chat_id}}
            ]
        return web.json_response({"kind": "youtube#liveBroadcastListResponse", "items": items})

    async def _list(self, request: web.Request) -> web.Response:
        if (denied := self._gate(request, "list")) is not None:
            return denied
        if request.query.get("liveChatId") != self.live_chat_id:
            return self._error(404, "liveChatNotFound")
        if self.chat_ended:
            return self._error(403, "liveChatEnded")
        limit = int(request.query.get("maxResults", "500"))
        token = request.query.get("pageToken")
        if token is None:
            start = max(0, len(self.items) - limit)
        elif token.startswith("t") and token[1:].isdigit() and int(token[1:]) <= len(self.items):
            start = int(token[1:])
        else:
            return self._error(400, "invalidPageToken")
        page = self.items[start : start + limit]
        return web.json_response(
            {
                "kind": "youtube#liveChatMessageListResponse",
                "pollingIntervalMillis": self.polling_interval_ms,
                "nextPageToken": f"t{start + len(page)}",
                "items": page,
            }
        )

    async def _delete(self, request: web.Request) -> web.Response:
        if (denied := self._gate(request, "delete")) is not None:
            return denied
        mid = request.query.get("id", "")
        if not any(item["id"] == mid for item in self.items):
            return self._error(404, "messageNotFound")
        self.deleted.append(mid)
        return web.Response(status=204)

    async def _ban(self, request: web.Request) -> web.Response:
        if (denied := self._gate(request, "ban")) is not None:
            return denied
        body = await request.json()
        self.bans.append(body["snippet"])
        return web.json_response({"kind": "youtube#liveChatBan", "snippet": body["snippet"]})


async def simulate_browser(session: Any, auth_url: str) -> None:
    """Act as the user's browser: open the consent URL, approve, follow the redirect to the loopback."""
    async with session.get(auth_url, allow_redirects=False) as consent:
        location = consent.headers.get("Location")
    if location:
        async with session.get(location, allow_redirects=False) as callback:
            await callback.read()
