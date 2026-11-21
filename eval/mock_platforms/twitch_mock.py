"""Twitch device-code OAuth, EventSub WebSocket and Helix moderation, as a loopback aiohttp server.

HTTP routes (real paths): ``POST /oauth2/device``, ``POST /oauth2/token`` (device grant until ``approve()``; refresh
tokens are single use), ``GET /oauth2/validate``, ``GET /helix/users``, ``POST /helix/eventsub/subscriptions``,
``DELETE /helix/moderation/chat``, ``POST /helix/moderation/bans``. WebSocket: ``/ws`` (``session_welcome``,
``session_keepalive``, ``notification``, ``session_reconnect``, ``revocation``). Helix checks the token's scopes
(``401`` when one is missing, as Twitch does) and that ``moderator_id`` is the token's user; a broadcaster or a
moderator cannot be moderated. ``drop()`` kills the sockets abruptly, ``send_reconnect()`` runs the handover.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

SCOPES = ("user:read:chat", "moderator:manage:chat_messages", "moderator:manage:banned_users")
TOPIC = "channel.chat.message"


def _ts() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class _Session:
    sid: str
    ws: web.WebSocketResponse
    transport: Any
    subscribed: bool = False
    retired: bool = False
    task: asyncio.Task[None] | None = None


@dataclass
class TwitchMock:
    broadcaster_id: str = "1001"
    user_id: str = "1001"  # the authorised account (the streamer, or a moderator of the channel)
    login: str = "specimen_streamer"
    client_id: str = "specimen-twitch-client"
    keepalive_s: int = 10
    token_lifetime_s: int = 3600
    is_moderator: bool = True  # the authorised account may moderate the channel
    origin: str = ""
    ws_url: str = ""
    sessions: list[_Session] = field(default_factory=list)
    connections: int = 0
    subscriptions: int = 0
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    bans: list[dict[str, Any]] = field(default_factory=list)
    helix_calls: list[tuple[str, str, int]] = field(default_factory=list)  # (method, path, status)
    _approved: bool = False
    _tokens: dict[str, dict[str, Any]] = field(default_factory=dict)  # access -> {user_id, scopes}
    _refresh: dict[str, dict[str, Any]] = field(default_factory=dict)
    _missing_scopes: set[str] = field(default_factory=set)
    _rate_limit: deque[int] = field(default_factory=deque)
    _reconnect_tokens: set[str] = field(default_factory=set)
    _silent: bool = False
    _runner: web.AppRunner | None = None
    _seq: int = 0

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/oauth2/device", self._device)
        app.router.add_post("/oauth2/token", self._token)
        app.router.add_get("/oauth2/validate", self._validate)
        app.router.add_get("/helix/users", self._users)
        app.router.add_post("/helix/eventsub/subscriptions", self._subscribe)
        app.router.add_delete("/helix/moderation/chat", self._delete)
        app.router.add_post("/helix/moderation/bans", self._ban)
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.origin = f"http://127.0.0.1:{port}"
        self.ws_url = f"ws://127.0.0.1:{port}/ws"
        return self.origin

    async def stop(self) -> None:
        for s in list(self.sessions):
            s.transport.abort() if s.transport is not None else None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------ test controls
    def approve(self) -> None:
        """The user has entered the device code on twitch.tv/activate."""
        self._approved = True

    def mint_tokens(self, scopes: tuple[str, ...] = SCOPES) -> tuple[str, str]:
        """A ready-made (access, refresh) pair, as a finished login would hold."""
        return self._mint(self.user_id, list(scopes))

    def lose_scope(self, scope: str) -> None:
        """Helix starts answering 401 'Missing scope' for ``scope`` while /validate still lists it (a scope revoked
        after the hourly validation)."""
        self._missing_scopes.add(scope)

    def invalidate_tokens(self) -> None:
        self._tokens.clear()

    def rate_limit_next(self, n: int = 1) -> None:
        self._rate_limit.extend([429] * n)

    def silence(self, on: bool = True) -> None:
        """Stop keepalives and notifications (the client must notice and reconnect)."""
        self._silent = on

    @property
    def live_sessions(self) -> list[_Session]:
        return [s for s in self.sessions if not s.ws.closed and not s.retired]

    async def send_chat(
        self, text: str, chatter_id: str = "2002", chatter_name: str = "viewer", *, badges: tuple[str, ...] = (),
        message_id: str | None = None, repeat: int = 1, notification_id: str | None = None,
    ) -> str:  # fmt: skip
        """Deliver one chat message as an EventSub notification (``repeat`` > 1 redelivers it, fresh notification id
        each time, as Twitch may after a resubscription)."""
        mid = message_id or str(uuid.uuid4())
        self.messages[mid] = {"chatter": chatter_id, "badges": badges, "text_len": len(text)}
        for i in range(repeat):
            env = self._notification(
                text,
                chatter_id,
                chatter_name,
                badges,
                mid,
                notification_id if (notification_id and i == 0) else str(uuid.uuid4()),
            )
            for s in self.live_sessions:
                if s.subscribed and not self._silent:
                    await s.ws.send_str(json.dumps(env))
        return mid

    async def send_reconnect(self) -> str:
        """Tell the current socket to move to a new URL (the new connection carries the subscription over)."""
        token = uuid.uuid4().hex
        self._reconnect_tokens.add(token)
        url = f"{self.ws_url}?reconnect={token}"
        for s in self.live_sessions:
            env = {
                "metadata": self._meta("session_reconnect"),
                "payload": {
                    "session": {
                        "id": s.sid,
                        "status": "reconnecting",
                        "keepalive_timeout_seconds": None,
                        "reconnect_url": url,
                        "connected_at": _ts(),
                    }
                },
            }
            await s.ws.send_str(json.dumps(env))
        return token

    def drop(self) -> int:
        """Kill every socket without a close handshake. Returns how many were open."""
        n = 0
        for s in self.live_sessions:
            n += 1
            if s.transport is not None:
                s.transport.abort()
        return n

    async def revoke(self) -> None:
        for s in self.live_sessions:
            env = {
                "metadata": self._meta("revocation", sub=True),
                "payload": {
                    "subscription": {
                        "id": "sub-specimen",
                        "status": "authorization_revoked",
                        "type": TOPIC,
                        "version": "1",
                    }
                },
            }
            await s.ws.send_str(json.dumps(env))

    # ------------------------------------------------------------------ envelopes
    def _meta(self, kind: str, *, sub: bool = False, nid: str | None = None) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "message_id": nid or str(uuid.uuid4()),
            "message_type": kind,
            "message_timestamp": _ts(),
        }
        if sub:
            meta.update(subscription_type=TOPIC, subscription_version="1")
        return meta

    def _notification(
        self, text: str, chatter_id: str, name: str, badges: tuple[str, ...], mid: str, nid: str
    ) -> dict[str, Any]:
        cond = {"broadcaster_user_id": self.broadcaster_id, "user_id": self.user_id}
        return {
            "metadata": self._meta("notification", sub=True, nid=nid),
            "payload": {
                "subscription": {
                    "id": "sub-specimen",
                    "status": "enabled",
                    "type": TOPIC,
                    "version": "1",
                    "condition": cond,
                    "transport": {"method": "websocket"},
                    "created_at": _ts(),
                    "cost": 0,
                },
                "event": {
                    "broadcaster_user_id": self.broadcaster_id,
                    "broadcaster_user_login": self.login,
                    "chatter_user_id": chatter_id,
                    "chatter_user_login": name,
                    "chatter_user_name": name,
                    "message_id": mid,
                    "message": {"text": text, "fragments": [{"type": "text", "text": text}]},
                    "color": "#00FF7F",
                    "badges": [{"set_id": b, "id": "1", "info": ""} for b in badges],
                    "message_type": "text",
                    "source_broadcaster_user_id": None,
                },
            },
        }

    # ------------------------------------------------------------------ oauth
    def _mint(self, user_id: str, scopes: list[str]) -> tuple[str, str]:
        self._seq += 1
        access, refresh = f"specimen-tw-access-{self._seq}", f"specimen-tw-refresh-{self._seq}"
        self._tokens[access] = {"user_id": user_id, "scopes": scopes}
        self._refresh[refresh] = {"user_id": user_id, "scopes": scopes}
        return access, refresh

    def _token_body(self, user_id: str, scopes: list[str]) -> dict[str, Any]:
        access, refresh = self._mint(user_id, scopes)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": self.token_lifetime_s,
            "scope": scopes,
            "token_type": "bearer",
        }

    async def _device(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("client_id") != self.client_id:
            return web.json_response({"status": 400, "message": "invalid client"}, status=400)
        return web.json_response(
            {
                "device_code": "specimen-device-code",
                "expires_in": 1800,
                "interval": 5,
                "user_code": "SPCMN123",
                "verification_uri": "https://www.twitch.tv/activate",
            }
        )

    async def _token(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("client_id") != self.client_id:
            return web.json_response({"status": 400, "message": "invalid client"}, status=400)
        grant = form.get("grant_type", "")
        if grant.endswith("device_code"):
            if form.get("device_code") != "specimen-device-code":
                return web.json_response({"status": 400, "message": "invalid device code"}, status=400)
            if not self._approved:
                return web.json_response({"status": 400, "message": "authorization_pending"}, status=400)
            return web.json_response(self._token_body(self.user_id, list(SCOPES)))
        if grant == "refresh_token":
            rec = self._refresh.pop(str(form.get("refresh_token")), None)  # single use
            if rec is None:
                return web.json_response({"status": 400, "message": "Invalid refresh token"}, status=400)
            return web.json_response(self._token_body(rec["user_id"], rec["scopes"]))
        return web.json_response({"status": 400, "message": "unsupported grant"}, status=400)

    async def _validate(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        rec = self._tokens.get(auth[6:]) if auth.startswith("OAuth ") else None
        if rec is None:
            return web.json_response({"status": 401, "message": "invalid access token"}, status=401)
        return web.json_response(
            {
                "client_id": self.client_id,
                "login": self.login,
                "scopes": rec["scopes"],
                "user_id": rec["user_id"],
                "expires_in": self.token_lifetime_s,
            }
        )

    # ------------------------------------------------------------------ helix
    def _helix_auth(
        self, request: web.Request, need: str | None
    ) -> tuple[dict[str, Any] | None, web.Response | None]:
        auth = request.headers.get("Authorization", "")
        rec = self._tokens.get(auth[7:]) if auth.startswith("Bearer ") else None
        if rec is None or request.headers.get("Client-Id") != self.client_id:
            return None, web.json_response({"status": 401, "message": "Invalid OAuth token"}, status=401)
        if need is not None and (need not in rec["scopes"] or need in self._missing_scopes):
            return None, web.json_response({"status": 401, "message": f"Missing scope: {need}"}, status=401)
        return rec, None

    def _log(self, request: web.Request, resp: web.Response) -> web.Response:
        self.helix_calls.append((request.method, request.path, resp.status))
        return resp

    def _limited(self, request: web.Request) -> web.Response | None:
        if self._rate_limit:
            self._rate_limit.popleft()
            return web.json_response(
                {"status": 429, "message": "Too Many Requests"},
                status=429,
                headers={"Ratelimit-Reset": str(int(time.time()) + 3)},
            )
        return None

    def _moderation_gate(self, request: web.Request, rec: dict[str, Any]) -> web.Response | None:
        q = request.query
        if q.get("broadcaster_id") != self.broadcaster_id:
            return web.json_response(
                {"status": 400, "message": "The broadcaster_id is not valid"}, status=400
            )
        if q.get("moderator_id") != rec["user_id"]:
            return web.json_response(
                {"status": 403, "message": "The user in moderator_id is not the token's user"}, status=403
            )
        if rec["user_id"] != self.broadcaster_id and not self.is_moderator:
            return web.json_response(
                {"status": 403, "message": "The user is not a moderator of the broadcaster's chat"},
                status=403,
            )
        return None

    async def _users(self, request: web.Request) -> web.Response:
        rec, err = self._helix_auth(request, None)
        if err:
            return self._log(request, err)
        return self._log(
            request,
            web.json_response(
                {"data": [{"id": rec["user_id"], "login": self.login, "display_name": self.login}]}
            ),
        )  # type: ignore[index]

    async def _delete(self, request: web.Request) -> web.Response:
        rec, err = self._helix_auth(request, "moderator:manage:chat_messages")
        if err:
            return self._log(request, err)
        assert rec is not None
        limited = self._limited(request) or self._moderation_gate(request, rec)
        if limited:
            return self._log(request, limited)
        mid = request.query.get("message_id", "")
        msg = self.messages.get(mid)
        if msg is None or mid in self.deleted:
            return self._log(
                request, web.json_response({"status": 404, "message": "message not found"}, status=404)
            )
        if {"broadcaster", "moderator"} & set(msg["badges"]):
            return self._log(
                request,
                web.json_response(
                    {"status": 400, "message": "cannot delete a broadcaster or moderator message"}, status=400
                ),
            )
        self.deleted.append(mid)
        return self._log(request, web.Response(status=204))

    async def _ban(self, request: web.Request) -> web.Response:
        rec, err = self._helix_auth(request, "moderator:manage:banned_users")
        if err:
            return self._log(request, err)
        assert rec is not None
        limited = self._limited(request) or self._moderation_gate(request, rec)
        if limited:
            return self._log(request, limited)
        try:
            data = (await request.json())["data"]
            user, duration = str(data["user_id"]), data.get("duration")
        except (ValueError, KeyError, TypeError):
            return self._log(
                request, web.json_response({"status": 400, "message": "invalid body"}, status=400)
            )
        if duration is not None and (not isinstance(duration, int) or not 1 <= duration <= 1_209_600):
            return self._log(
                request, web.json_response({"status": 400, "message": "invalid duration"}, status=400)
            )
        if user == self.broadcaster_id:
            return self._log(
                request,
                web.json_response({"status": 400, "message": "the broadcaster cannot be banned"}, status=400),
            )
        if any(
            v["chatter"] == user and {"moderator", "broadcaster"} & set(v["badges"])
            for v in self.messages.values()
        ):
            return self._log(
                request,
                web.json_response({"status": 400, "message": "moderators cannot be banned"}, status=400),
            )
        self.bans.append({"user_id": user, "duration": duration})
        now = _ts()
        return self._log(
            request,
            web.json_response(
                {
                    "data": [
                        {
                            "broadcaster_id": self.broadcaster_id,
                            "moderator_id": rec["user_id"],
                            "user_id": user,
                            "created_at": now,
                            "end_time": now if duration else None,
                        }
                    ]
                }
            ),
        )

    async def _subscribe(self, request: web.Request) -> web.Response:
        rec, err = self._helix_auth(request, "user:read:chat")
        if err:
            return self._log(request, err)
        try:
            body = await request.json()
            sid = body["transport"]["session_id"]
            cond = body["condition"]
            if body["type"] != TOPIC or body["version"] != "1" or body["transport"]["method"] != "websocket":
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._log(
                request, web.json_response({"status": 400, "message": "invalid subscription"}, status=400)
            )
        session = next((s for s in self.live_sessions if s.sid == sid), None)
        if session is None:
            return self._log(
                request,
                web.json_response(
                    {
                        "status": 400,
                        "message": "websocket transport session does not exist or has already disconnected",
                    },
                    status=400,
                ),
            )
        if cond.get("broadcaster_user_id") != self.broadcaster_id or cond.get("user_id") != (rec or {}).get(
            "user_id"
        ):
            return self._log(
                request,
                web.json_response(
                    {"status": 403, "message": "condition does not match the token"}, status=403
                ),
            )
        session.subscribed = True
        self.subscriptions += 1
        record = {
            "id": f"sub-{self.subscriptions}",
            "status": "enabled",
            "type": TOPIC,
            "version": "1",
            "condition": cond,
            "transport": {"method": "websocket", "session_id": sid},
            "created_at": _ts(),
            "cost": 0,
        }
        return self._log(
            request,
            web.json_response(
                {"data": [record], "total": 1, "total_cost": 0, "max_total_cost": 10}, status=202
            ),
        )

    # ------------------------------------------------------------------ websocket
    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(request)
        self.connections += 1
        token = request.query.get("reconnect")
        carried = token in self._reconnect_tokens
        old = list(self.live_sessions)
        sid = old[0].sid if carried and old else f"specimen-session-{uuid.uuid4().hex[:8]}"
        session = _Session(sid, ws, request.transport, subscribed=carried)
        self.sessions.append(session)
        welcome = {
            "metadata": self._meta("session_welcome"),
            "payload": {
                "session": {
                    "id": sid,
                    "status": "connected",
                    "connected_at": _ts(),
                    "keepalive_timeout_seconds": self.keepalive_s,
                    "reconnect_url": None,
                }
            },
        }
        await ws.send_str(json.dumps(welcome))
        if carried:
            for o in old:  # the old socket stops receiving the moment the new one is welcomed
                o.retired = True
        session.task = asyncio.ensure_future(self._keepalive(session))
        try:
            async for _ in ws:  # the client never sends data; this just waits for the close
                pass
        finally:
            session.task.cancel()
        return ws

    async def _keepalive(self, s: _Session) -> None:
        try:
            while not s.ws.closed:
                await asyncio.sleep(max(0.2, self.keepalive_s / 2))
                if not self._silent and not s.ws.closed:
                    await s.ws.send_str(
                        json.dumps({"metadata": self._meta("session_keepalive"), "payload": {}})
                    )
        except (asyncio.CancelledError, ConnectionError):
            pass
