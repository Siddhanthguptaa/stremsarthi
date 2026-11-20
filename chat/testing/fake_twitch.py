"""Fake Twitch server (loopback only): device-code OAuth, Helix, and an EventSub WebSocket.

Mirrors the documented message shapes: ``session_welcome``, ``session_keepalive``, ``notification``,
``session_reconnect`` and ``revocation``. Tests script failures (drop, silence, reconnect, duplicate
notifications) and read what the client did (subscriptions, deletes, bans). Synthetic data only.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiohttp import WSMsgType, web

from .waiting import until

SCOPES = ["user:read:chat", "moderator:manage:chat_messages", "moderator:manage:banned_users"]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class _Socket:
    ws: web.WebSocketResponse
    session_id: str
    keepalive_task: asyncio.Task[None] | None = None
    silenced: bool = False
    subscribed: bool = False


@dataclass
class FakeTwitch:
    client_id: str = "specimen-twitch-client"
    user_id: str = "1001"
    keepalive_timeout_s: int = 1
    keepalive_interval_s: float = 0.2
    scopes: list[str] = field(default_factory=lambda: list(SCOPES))
    approved: bool = False
    pending_polls_before_slow_down: int = 0
    refresh_tokens: set[str] = field(default_factory=set)
    valid_tokens: set[str] = field(default_factory=set)
    sockets: list[_Socket] = field(default_factory=list)
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    delete_queries: list[dict[str, str]] = field(default_factory=list)
    bans: list[dict[str, Any]] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    helix_headers: list[dict[str, str]] = field(default_factory=list)
    connections: int = 0
    welcome_delay_s: float = 0.0
    subscribe_status: int = 202
    helix_failures: list[tuple[int, dict[str, str]]] = field(default_factory=list)
    origin: str = ""
    ws_url: str = ""
    _runner: web.AppRunner | None = None

    # ---- lifecycle ----------------------------------------------------------------------------
    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/oauth2/device", self._device)
        app.router.add_post("/oauth2/token", self._token)
        app.router.add_get("/oauth2/validate", self._validate)
        app.router.add_post("/helix/eventsub/subscriptions", self._subscribe)
        app.router.add_delete("/helix/moderation/chat", self._delete)
        app.router.add_post("/helix/moderation/bans", self._ban)
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.origin = f"http://127.0.0.1:{port}"
        self.ws_url = f"ws://127.0.0.1:{port}/ws"
        return self.origin

    async def stop(self) -> None:
        await self.drop()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ---- scripting API ------------------------------------------------------------------------
    def approve(self) -> None:
        self.approved = True

    def live_sockets(self) -> list[_Socket]:
        return [s for s in self.sockets if not s.ws.closed]

    async def wait_subscribed(self, count: int = 1, timeout_s: float = 5.0) -> None:
        await until(lambda: sum(1 for s in self.live_sockets() if s.subscribed) >= count, timeout_s)

    def notification(
        self,
        text: str,
        *,
        chatter_id: str = "2002",
        chatter_name: str = "viewer",
        message_id: str | None = None,
        notification_id: str | None = None,
        badges: list[str] | None = None,
        broadcaster_id: str | None = None,
        source_broadcaster_id: str | None = None,
    ) -> dict[str, Any]:
        broadcaster = broadcaster_id or self.user_id
        return {
            "metadata": {
                "message_id": notification_id or str(uuid.uuid4()),
                "message_type": "notification",
                "message_timestamp": _now(),
                "subscription_type": "channel.chat.message",
                "subscription_version": "1",
            },
            "payload": {
                "subscription": {
                    "id": "sub-1",
                    "status": "enabled",
                    "type": "channel.chat.message",
                    "version": "1",
                    "condition": {"broadcaster_user_id": broadcaster, "user_id": self.user_id},
                    "transport": {"method": "websocket", "session_id": "x"},
                    "created_at": _now(),
                },
                "event": {
                    "broadcaster_user_id": broadcaster,
                    "broadcaster_user_login": "specimen",
                    "broadcaster_user_name": "Specimen",
                    "chatter_user_id": chatter_id,
                    "chatter_user_login": "viewer",
                    "chatter_user_name": chatter_name,
                    "message_id": message_id or str(uuid.uuid4()),
                    "message": {"text": text, "fragments": [{"type": "text", "text": text}]},
                    "message_type": "text",
                    "badges": [{"set_id": b, "id": "1", "info": ""} for b in (badges or [])],
                    "source_broadcaster_user_id": source_broadcaster_id,
                },
            },
        }

    async def push(self, envelope: dict[str, Any]) -> int:
        """Send one envelope to every subscribed socket; returns how many received it."""
        sent = 0
        for sock in self.live_sockets():
            if sock.subscribed:
                await sock.ws.send_json(envelope)
                sent += 1
        return sent

    async def push_chat(self, text: str, **kwargs: Any) -> dict[str, Any]:
        envelope = self.notification(text, **kwargs)
        await self.push(envelope)
        return envelope

    async def drop(self) -> None:
        """Abruptly close every socket (network loss)."""
        for sock in list(self.sockets):
            if sock.keepalive_task:
                sock.keepalive_task.cancel()
            with contextlib.suppress(Exception):
                await sock.ws.close()

    def silence(self) -> None:
        """Stop keepalives (and everything else) on live sockets: the client must time out."""
        for sock in self.live_sockets():
            sock.silenced = True

    async def send_reconnect(self) -> None:
        for sock in self.live_sockets():
            await sock.ws.send_json(
                {
                    "metadata": {
                        "message_id": str(uuid.uuid4()),
                        "message_type": "session_reconnect",
                        "message_timestamp": _now(),
                    },
                    "payload": {
                        "session": {
                            "id": sock.session_id,
                            "status": "reconnecting",
                            "keepalive_timeout_seconds": None,
                            "reconnect_url": self.ws_url + "?reconnect=1",
                        }
                    },
                }
            )

    async def send_revocation(self) -> None:
        for sock in self.live_sockets():
            await sock.ws.send_json(
                {
                    "metadata": {
                        "message_id": str(uuid.uuid4()),
                        "message_type": "revocation",
                        "message_timestamp": _now(),
                        "subscription_type": "channel.chat.message",
                        "subscription_version": "1",
                    },
                    "payload": {
                        "subscription": {"status": "authorization_revoked", "type": "channel.chat.message"}
                    },
                }
            )

    def fail_next_helix(self, status: int, headers: dict[str, str] | None = None, times: int = 1) -> None:
        self.helix_failures.extend([(status, headers or {})] * times)

    # ---- OAuth --------------------------------------------------------------------------------
    async def _device(self, request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("client_id") != self.client_id:
            return web.json_response({"status": 400, "message": "invalid client"}, status=400)
        return web.json_response(
            {
                "device_code": "device-" + secrets.token_hex(6),
                "expires_in": 1800,
                "interval": 1,
                "user_code": "ABCDEFGH",
                "verification_uri": "https://www.twitch.tv/activate?device-code=ABCDEFGH",
            }
        )

    def _issue(self) -> dict[str, Any]:
        access, refresh = "access-" + secrets.token_hex(8), "refresh-" + secrets.token_hex(8)
        self.valid_tokens.add(access)
        self.refresh_tokens.add(refresh)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 14000,
            "scope": list(self.scopes),
            "token_type": "bearer",
        }

    async def _token(self, request: web.Request) -> web.Response:
        form = {k: str(v) for k, v in (await request.post()).items()}
        self.token_requests.append(
            {k: v for k, v in form.items() if k not in ("device_code", "refresh_token")}
        )
        grant = form.get("grant_type", "")
        if grant.endswith("device_code"):
            if self.pending_polls_before_slow_down > 0:
                self.pending_polls_before_slow_down -= 1
                return web.json_response({"status": 400, "message": "slow_down"}, status=400)
            if not self.approved:
                return web.json_response({"status": 400, "message": "authorization_pending"}, status=400)
            return web.json_response(self._issue())
        if grant == "refresh_token":
            old = form.get("refresh_token", "")
            if old not in self.refresh_tokens:
                return web.json_response({"status": 400, "message": "Invalid refresh token"}, status=400)
            self.refresh_tokens.discard(old)  # public-client refresh tokens are single use
            return web.json_response(self._issue())
        return web.json_response({"status": 400, "message": "bad grant"}, status=400)

    async def _validate(self, request: web.Request) -> web.Response:
        token = request.headers.get("Authorization", "")[6:]
        if token not in self.valid_tokens:
            return web.json_response({"status": 401, "message": "invalid access token"}, status=401)
        return web.json_response(
            {
                "client_id": self.client_id,
                "login": "specimen",
                "scopes": list(self.scopes),
                "user_id": self.user_id,
                "expires_in": 13000,
            }
        )

    # ---- Helix --------------------------------------------------------------------------------
    def _helix_gate(self, request: web.Request) -> web.Response | None:
        self.helix_headers.append(
            {k: v for k, v in request.headers.items() if k in ("Client-Id", "Authorization")}
        )
        if request.headers.get("Authorization", "")[7:] not in self.valid_tokens:
            return web.json_response({"status": 401, "message": "Invalid OAuth token"}, status=401)
        if self.helix_failures:
            status, headers = self.helix_failures.pop(0)
            return web.json_response(
                {"status": status, "message": "specimen"}, status=status, headers=headers
            )
        return None

    async def _subscribe(self, request: web.Request) -> web.Response:
        if (denied := self._helix_gate(request)) is not None:
            return denied
        body = await request.json()
        session_id = body.get("transport", {}).get("session_id")
        sock = next((s for s in self.live_sockets() if s.session_id == session_id), None)
        if sock is None or self.subscribe_status != 202:
            status = self.subscribe_status if self.subscribe_status != 202 else 400
            return web.json_response({"status": status, "message": "specimen"}, status=status)
        sock.subscribed = True
        self.subscriptions.append(body)
        record = {
            "id": str(uuid.uuid4()),
            "status": "enabled",
            "type": body["type"],
            "version": body["version"],
            "condition": body["condition"],
            "transport": {"method": "websocket", "session_id": session_id},
        }
        return web.json_response({"data": [record], "total": 1}, status=202)

    async def _delete(self, request: web.Request) -> web.Response:
        if (denied := self._helix_gate(request)) is not None:
            return denied
        self.deleted.append(request.query.get("message_id", ""))
        self.delete_queries.append(dict(request.query))
        return web.Response(status=204)

    async def _ban(self, request: web.Request) -> web.Response:
        if (denied := self._helix_gate(request)) is not None:
            return denied
        body = await request.json()
        self.bans.append({"query": dict(request.query), **body})
        return web.json_response({"data": [{"user_id": body["data"]["user_id"]}]})

    # ---- EventSub WebSocket -------------------------------------------------------------------
    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        reconnect = request.query.get("reconnect") == "1"
        sock = _Socket(ws, "session-" + secrets.token_hex(6))
        if reconnect:  # subscriptions carry over to the new session
            sock.subscribed = any(s.subscribed for s in self.sockets)
        self.sockets.append(sock)
        if self.welcome_delay_s:
            await asyncio.sleep(self.welcome_delay_s)
        await ws.send_json(
            {
                "metadata": {
                    "message_id": str(uuid.uuid4()),
                    "message_type": "session_welcome",
                    "message_timestamp": _now(),
                },
                "payload": {
                    "session": {
                        "id": sock.session_id,
                        "status": "connected",
                        "keepalive_timeout_seconds": self.keepalive_timeout_s,
                        "reconnect_url": None,
                    }
                },
            }
        )
        sock.keepalive_task = asyncio.create_task(self._keepalives(sock))
        try:
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            sock.keepalive_task.cancel()
        return ws

    async def _keepalives(self, sock: _Socket) -> None:
        while not sock.ws.closed:
            await asyncio.sleep(self.keepalive_interval_s)
            if sock.silenced or sock.ws.closed:
                continue
            with contextlib.suppress(Exception):
                await sock.ws.send_json(
                    {
                        "metadata": {
                            "message_id": str(uuid.uuid4()),
                            "message_type": "session_keepalive",
                            "message_timestamp": _now(),
                        },
                        "payload": {},
                    }
                )
