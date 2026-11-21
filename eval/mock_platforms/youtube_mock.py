"""YouTube Data API v3 (live chat) and Google OAuth refresh, as a loopback aiohttp server.

Routes (the real paths): ``POST /token``, ``GET /youtube/v3/liveBroadcasts``, ``GET /youtube/v3/liveChat/messages``
(liveChatMessages.list, honours ``pageToken``, returns ``pollingIntervalMillis``), ``DELETE
/youtube/v3/liveChat/messages?id=`` (liveChatMessages.delete), ``POST /youtube/v3/liveChat/bans`` (liveChatBans.insert)
and, for tests, ``GET /_quota``. Quota units per call: list 5, delete 20, ban 200, broadcast lookup 1; the
10,000-unit day is enforced (``403 quotaExceeded``). Failures are injectable per call kind.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web

SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
COSTS = {"list": 5, "lookup": 1, "delete": 20, "ban": 200}
ROUTE_KIND = {
    ("GET", "/youtube/v3/liveBroadcasts"): "lookup",
    ("GET", "/youtube/v3/liveChat/messages"): "list",
    ("DELETE", "/youtube/v3/liveChat/messages"): "delete",
    ("POST", "/youtube/v3/liveChat/bans"): "ban",
}


@dataclass
class Injected:
    kind: str  # list | lookup | delete | ban | token
    status: int
    reason: str
    times: int


@dataclass
class Call:
    kind: str
    status: int
    cost: int
    at: float


@dataclass
class YouTubeMock:
    live_chat_id: str = "LIVECHAT_SPECIMEN_1"
    daily_quota: int = 10_000
    polling_interval_ms: int = 5000
    client_id: str = "specimen-client-id"
    refresh_token: str = "specimen-refresh-token"
    token_lifetime_s: int = 3600
    now: Callable[[], float] = time.time
    broadcast_active: bool = True
    can_moderate: bool = True  # the authorised account may delete and ban
    units_used: int = 0
    units_by_kind: Counter[str] = field(default_factory=Counter)
    calls_by_kind: Counter[str] = field(default_factory=Counter)
    calls: list[Call] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)  # the feed, in order
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> text item
    deleted: list[str] = field(default_factory=list)
    bans: list[dict[str, Any]] = field(default_factory=list)
    token_requests: int = 0
    _injected: deque[Injected] = field(default_factory=deque)
    _valid_access: set[str] = field(default_factory=set)
    _seq: int = 0
    _day: Any = None
    _runner: web.AppRunner | None = None
    origin: str = ""

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/token", self._token)
        app.router.add_route("*", "/youtube/v3/liveBroadcasts", self._api)
        app.router.add_route("*", "/youtube/v3/liveChat/messages", self._api)
        app.router.add_route("*", "/youtube/v3/liveChat/bans", self._api)
        app.router.add_get("/_quota", self._quota)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.origin = f"http://127.0.0.1:{port}"
        return self.origin

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------ test controls
    def inject(self, kind: str, status: int, reason: str = "", times: int = 1) -> None:
        """Answer the next ``times`` calls of ``kind`` with this error (still charged to the quota)."""
        self._injected.append(Injected(kind, status, reason, times))

    def expire_access_token(self) -> None:
        """The current access token stops working: the next call is 401 until the client refreshes."""
        self._valid_access.clear()

    def add_message(
        self, text: str, author_id: str = "UC_SPECIMEN_VIEWER", display: str = "viewer", *,
        owner: bool = False, moderator: bool = False, published_at: float | None = None, message_id: str | None = None,
    ) -> str:  # fmt: skip
        self._seq += 1
        mid = message_id or f"MSG_SPECIMEN_{self._seq:06d}"
        at = datetime.fromtimestamp(published_at if published_at is not None else self.now(), UTC)
        item = {
            "kind": "youtube#liveChatMessage",
            "id": mid,
            "snippet": {
                "type": "textMessageEvent",
                "liveChatId": self.live_chat_id,
                "authorChannelId": author_id,
                "publishedAt": at.isoformat().replace("+00:00", "Z"),
                "hasDisplayContent": True,
                "displayMessage": text,
                "textMessageDetails": {"messageText": text},
            },
            "authorDetails": {
                "channelId": author_id,
                "displayName": display,
                "isChatOwner": owner,
                "isChatModerator": moderator,
            },
        }
        self.items.append(item)
        self.messages[mid] = item
        return mid

    def end_chat(self) -> None:
        self._seq += 1
        self.items.append(
            {
                "kind": "youtube#liveChatMessage",
                "id": f"END_{self._seq}",
                "snippet": {"type": "chatEndedEvent", "liveChatId": self.live_chat_id},
            }
        )

    @property
    def quota_snapshot(self) -> dict[str, Any]:
        return {
            "daily_quota": self.daily_quota,
            "units_used": self.units_used,
            "units_by_kind": dict(self.units_by_kind),
            "calls_by_kind": dict(self.calls_by_kind),
        }

    # ------------------------------------------------------------------ handlers
    @staticmethod
    def _err(status: int, reason: str) -> web.Response:
        return web.json_response(
            {
                "error": {
                    "code": status,
                    "message": reason,
                    "errors": [{"reason": reason, "domain": "youtube.liveChat"}],
                }
            },
            status=status,
        )

    async def _quota(self, request: web.Request) -> web.Response:
        return web.json_response(self.quota_snapshot)

    async def _token(self, request: web.Request) -> web.Response:
        form = await request.post()
        self.token_requests += 1
        for inj in list(self._injected):
            if inj.kind == "token" and inj.times > 0:
                inj.times -= 1
                return web.json_response({"error": inj.reason or "server_error"}, status=inj.status)
        if form.get("client_id") != self.client_id:
            return web.json_response({"error": "invalid_client"}, status=401)
        if form.get("grant_type") != "refresh_token" or form.get("refresh_token") != self.refresh_token:
            return web.json_response({"error": "invalid_grant"}, status=400)
        token = f"specimen-access-{self.token_requests}"
        self._valid_access.add(token)
        return web.json_response(
            {
                "access_token": token,
                "expires_in": self.token_lifetime_s,
                "scope": SCOPE,
                "token_type": "Bearer",
            }
        )

    def issue_access_token(self) -> str:
        """A ready-made access token (what a finished OAuth login would hold)."""
        self.token_requests += 1
        token = f"specimen-access-{self.token_requests}"
        self._valid_access.add(token)
        return token

    async def _api(self, request: web.Request) -> web.Response:
        kind = ROUTE_KIND.get((request.method, request.path))
        if kind is None:
            return self._err(405, "methodNotAllowed")
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self._valid_access:
            return self._err(401, "authError")
        cost = COSTS[kind]
        day = datetime.fromtimestamp(self.now(), ZoneInfo("America/Los_Angeles")).date()
        if day != self._day:  # the quota resets at midnight Pacific
            self._day = day
            self.units_used = 0
        if self.units_used + cost > self.daily_quota:
            self._record(kind, 403, 0)
            return self._err(403, "quotaExceeded")
        self.units_used += cost
        self.units_by_kind[kind] += cost
        self.calls_by_kind[kind] += 1
        for inj in list(self._injected):
            if inj.kind == kind and inj.times > 0:
                inj.times -= 1
                self._record(kind, inj.status, cost)
                if inj.reason == "quotaExceeded":
                    self.units_used = self.daily_quota
                return self._err(inj.status, inj.reason or "backendError")
        resp = await getattr(self, f"_{kind}")(request)
        self._record(kind, resp.status, cost)
        return resp

    def _record(self, kind: str, status: int, cost: int) -> None:
        self.calls.append(Call(kind, status, cost, self.now()))

    async def _lookup(self, request: web.Request) -> web.Response:
        if request.query.get("broadcastStatus") != "active":
            return self._err(400, "invalidFilters")
        items = (
            [
                {
                    "kind": "youtube#liveBroadcast",
                    "id": "BCAST_SPECIMEN",
                    "snippet": {"liveChatId": self.live_chat_id, "title": "SPECIMEN stream"},
                }
            ]
            if self.broadcast_active
            else []
        )
        return web.json_response({"kind": "youtube#liveBroadcastListResponse", "items": items})

    async def _list(self, request: web.Request) -> web.Response:
        if request.query.get("liveChatId") != self.live_chat_id:
            return self._err(404, "liveChatNotFound")
        token = request.query.get("pageToken", "")
        try:
            start = int(token[1:]) if token else 0
            if token and (token[0] != "p" or start < 0 or start > len(self.items)):
                raise ValueError
        except ValueError:
            return self._err(400, "pageTokenInvalid")
        limit = int(request.query.get("maxResults", "500"))
        chunk = self.items[start : start + limit]
        nxt = start + len(chunk)
        return web.json_response(
            {
                "kind": "youtube#liveChatMessageListResponse",
                "pollingIntervalMillis": self.polling_interval_ms,
                "nextPageToken": f"p{nxt}",
                "pageInfo": {"totalResults": len(chunk), "resultsPerPage": limit},
                "items": chunk,
            }
        )

    async def _delete(self, request: web.Request) -> web.Response:
        if not self.can_moderate:
            return self._err(403, "insufficientPermissions")
        mid = request.query.get("id", "")
        if mid not in self.messages or mid in self.deleted:
            return self._err(404, "liveChatMessageNotFound")
        self.deleted.append(mid)
        self._seq += 1
        self.items.append(
            {
                "kind": "youtube#liveChatMessage",
                "id": f"DEL_{self._seq}",
                "snippet": {
                    "type": "messageDeletedEvent",
                    "liveChatId": self.live_chat_id,
                    "messageDeletedDetails": {"deletedMessageId": mid},
                },
            }
        )
        return web.Response(status=204)

    async def _ban(self, request: web.Request) -> web.Response:
        if not self.can_moderate:
            return self._err(403, "insufficientPermissions")
        if request.query.get("part") != "snippet":
            return self._err(400, "missingRequiredParameter")
        try:
            body = await request.json()
            snip = body["snippet"]
            ban_type = snip["type"]
            channel = snip["bannedUserDetails"]["channelId"]
            chat_id = snip["liveChatId"]
        except (ValueError, KeyError, TypeError):
            return self._err(400, "invalidSnippet")
        if chat_id != self.live_chat_id or ban_type not in ("temporary", "permanent"):
            return self._err(400, "invalidSnippet")
        if ban_type == "temporary" and not isinstance(snip.get("banDurationSeconds"), int):
            return self._err(400, "banDurationRequired")
        record = {
            "liveChatId": chat_id,
            "type": ban_type,
            "channelId": channel,
            "seconds": snip.get("banDurationSeconds"),
        }
        self.bans.append(record)
        return web.json_response(
            {"kind": "youtube#liveChatBan", "id": f"BAN_{len(self.bans)}", "snippet": snip}
        )
