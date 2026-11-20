"""EventSub WebSocket transport, written directly on aiohttp.

Why not ``twitchAPI.eventsub.websocket.EventSubWebsocket`` (4.5.0)? Its public surface is
``start()/stop()/listen_*()`` plus a ``revocation_handler``. It exposes no hook for socket loss,
keepalive timeout or reconnect, so the required ``twitch_disconnected`` status and generation fencing
cannot be built on it without patching private members, which D-14 forbids. It also runs on its own
thread and event loop and refreshes tokens with an app secret, while this client is a public
device-code client. The protocol is small, so this module implements it on aiohttp (reusing the
IRL Shield ideas: verified subscription responses, generation fencing, handover on
``session_reconnect``).

Flow: dial -> ``session_welcome`` -> ``POST /helix/eventsub/subscriptions`` for ``channel.chat.message``
(must happen within 10 s of the welcome) -> read notifications. ``session_reconnect`` dials the new
URL, waits for its welcome, then retires the old socket (subscriptions carry over; duplicates are
removed by ``ChatEvents``). A keepalive timeout or a closed socket raises a retryable error and the
``Connector`` base reconnects with backoff. A drop never asserts a hold.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import aiohttp

from chat.common.connector import Connector, FatalConnectorError, RetryableError
from chat.common.errors import ChatError
from chat.common.http import FixedOriginClient

from .auth import UserSession
from .errors import InvalidToken, TwitchError
from .events import TOPIC, ChatEvents, websocket_url

EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
_MAX_WS_MESSAGE = 1024 * 1024


class EventSubTransport(Connector):
    def __init__(
        self,
        events: ChatEvents,
        session: UserSession,
        http: FixedOriginClient,
        ws_session: aiohttp.ClientSession,
        *,
        broadcaster_id: str,
        url: str = EVENTSUB_URL,
        mock: bool = False,
        keepalive_grace_s: float = 5.0,
        welcome_timeout_s: float = 10.0,
        retire_grace_s: float = 1.0,
        **connector_kwargs: Any,
    ) -> None:
        super().__init__("twitch", **connector_kwargs)
        self.events = events
        self.session = session
        self.http = http
        self.ws_session = ws_session
        self.broadcaster_id = broadcaster_id
        self.url = websocket_url(url, mock=mock)
        self._mock = mock
        self._keepalive_grace_s = keepalive_grace_s
        self._welcome_timeout_s = welcome_timeout_s
        self._retire_grace_s = retire_grace_s
        self.notifications = 0

    # ---- socket helpers -----------------------------------------------------------------------
    async def _dial(self, url: str) -> aiohttp.ClientWebSocketResponse:
        try:
            return await asyncio.wait_for(
                self.ws_session.ws_connect(url, max_msg_size=_MAX_WS_MESSAGE, autoping=True), 20
            )
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise RetryableError("twitch_connect_failed") from None

    @staticmethod
    async def _pump(
        ws: aiohttp.ClientWebSocketResponse,
        conn: int,
        queue: asyncio.Queue[tuple[int, dict[str, Any] | None]],
    ) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    if isinstance(data, dict):
                        await queue.put((conn, data))
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except Exception:  # connection errors end the pump; the main loop sees the closed marker
            pass
        finally:
            await queue.put((conn, None))

    @staticmethod
    def _session_of(msg: dict[str, Any]) -> dict[str, Any]:
        try:
            info = msg["payload"]["session"]
        except (KeyError, TypeError):
            raise RetryableError("twitch_invalid_welcome") from None
        if not isinstance(info, dict) or not isinstance(info.get("id"), str) or not info["id"]:
            raise RetryableError("twitch_invalid_welcome")
        return info

    @staticmethod
    def _mtype(msg: dict[str, Any]) -> str:
        meta = msg.get("metadata")
        kind = meta.get("message_type") if isinstance(meta, dict) else None
        return kind if isinstance(kind, str) else ""

    async def _await_welcome(
        self, queue: asyncio.Queue[tuple[int, dict[str, Any] | None]], conn: int
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._welcome_timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RetryableError("twitch_welcome_timeout")
            try:
                got_conn, msg = await asyncio.wait_for(queue.get(), remaining)
            except TimeoutError:
                raise RetryableError("twitch_welcome_timeout") from None
            if got_conn != conn:
                continue
            if msg is None:
                raise RetryableError("twitch_socket_closed")
            if self._mtype(msg) == "session_welcome":
                return self._session_of(msg)

    async def _subscribe(self, session_id: str) -> None:
        try:
            token = await self.session.access_token()
        except InvalidToken:
            raise FatalConnectorError("twitch_auth_failed") from None
        user_id = self.session.user_id  # learned by the validation inside access_token()
        condition = {"broadcaster_user_id": self.broadcaster_id, "user_id": user_id}
        request = {
            "type": TOPIC,
            "version": "1",
            "condition": condition,
            "transport": {"method": "websocket", "session_id": session_id},
        }
        try:
            response = await self.http.request(
                "POST",
                "/helix/eventsub/subscriptions",
                headers={
                    "Client-Id": self.session.auth.client_id,
                    "Authorization": "Bearer " + token.reveal(),
                },
                json_body=request,
            )
        except TwitchError:
            raise
        except ChatError as exc:
            raise RetryableError(exc.code) from None
        if response.status == 401:
            raise FatalConnectorError("twitch_auth_failed")
        if response.status == 403:
            raise FatalConnectorError("twitch_forbidden")  # missing scope, or not allowed to read this chat
        records = response.body.get("data")
        record = records[0] if isinstance(records, list) and len(records) == 1 else None
        transport = record.get("transport", {}) if isinstance(record, dict) else {}
        if (
            response.status != 202
            or not isinstance(record, dict)
            or record.get("status") != "enabled"
            or record.get("type") != TOPIC
            or record.get("version") != "1"
            or record.get("condition") != condition
            or transport.get("method") != "websocket"
            or transport.get("session_id") != session_id
        ):
            raise RetryableError(
                "twitch_subscribe_failed" if response.status != 429 else "twitch_rate_limited"
            )

    # ---- session ------------------------------------------------------------------------------
    async def _run_session(self) -> None:
        gen = self.events.connection_started()
        queue: asyncio.Queue[tuple[int, dict[str, Any] | None]] = asyncio.Queue(maxsize=1024)
        sockets: dict[int, aiohttp.ClientWebSocketResponse] = {}
        tasks: set[asyncio.Future[Any]] = set()
        fatal = False
        try:
            sockets[0] = await self._dial(self.url)
            tasks.add(asyncio.ensure_future(self._pump(sockets[0], 0, queue)))
            info = await self._await_welcome(queue, 0)
            if not self.events.welcome(info["id"], generation=gen):
                raise RetryableError("twitch_welcome_rejected")
            keepalive = float(info.get("keepalive_timeout_seconds") or 10)
            await self._subscribe(info["id"])
            if not self.events.ready(generation=gen):
                raise RetryableError("twitch_not_ready")
            self.mark_healthy()
            await self._read_loop(gen, queue, sockets, tasks, keepalive)
        except FatalConnectorError as exc:
            fatal = exc.code in ("twitch_subscription_revoked", "twitch_auth_failed", "twitch_forbidden")
            self.events.fault(exc.code, revoked=fatal)
            raise
        except asyncio.CancelledError:
            self.events.fault("stopped", report=False)  # intentional shutdown is not an outage
            raise
        except BaseException as exc:
            self.events.fault(getattr(exc, "code", type(exc).__name__.lower()))
            raise
        finally:
            for task in tasks:
                task.cancel()
            for ws in sockets.values():
                with contextlib.suppress(Exception):
                    await ws.close()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read_loop(
        self,
        gen: int,
        queue: asyncio.Queue[tuple[int, dict[str, Any] | None]],
        sockets: dict[int, aiohttp.ClientWebSocketResponse],
        tasks: set[asyncio.Future[Any]],
        keepalive: float,
    ) -> None:
        current, pending, next_conn = 0, None, 1
        handover_deadline = 0.0
        while not self.stopped:
            wait = keepalive + self._keepalive_grace_s
            if pending is not None:
                wait = min(wait, max(0.01, handover_deadline - time.monotonic()))
            try:
                conn, msg = await asyncio.wait_for(queue.get(), wait)
            except TimeoutError:
                if pending is not None and time.monotonic() >= handover_deadline:
                    raise RetryableError("twitch_handover_failed") from None
                raise RetryableError("twitch_keepalive_timeout") from None
            if msg is None:
                if conn == current:
                    raise RetryableError("twitch_socket_closed")
                if conn == pending:
                    raise RetryableError("twitch_handover_failed")
                continue
            kind = self._mtype(msg)
            if kind == "notification":
                self.notifications += 1
                self.events.accept(msg, generation=gen)
            elif kind == "session_reconnect" and conn == current and pending is None:
                url = self._session_of(msg).get("reconnect_url")
                if not isinstance(url, str) or not self.events.reconnect(url, generation=gen):
                    raise RetryableError("twitch_bad_reconnect")
                pending, next_conn = next_conn, next_conn + 1
                handover_deadline = time.monotonic() + self._welcome_timeout_s
                sockets[pending] = await self._dial(websocket_url(url, mock=self._mock))
                tasks.add(asyncio.ensure_future(self._pump(sockets[pending], pending, queue)))
            elif kind == "session_welcome" and conn == pending:
                info = self._session_of(msg)
                if not self.events.handed_over(info["id"], generation=gen):
                    raise RetryableError("twitch_handover_failed")
                old, current, pending = current, conn, None
                keepalive = float(info.get("keepalive_timeout_seconds") or keepalive)
                tasks.add(asyncio.ensure_future(self._retire(sockets.pop(old))))
            elif kind == "revocation":
                raise FatalConnectorError("twitch_subscription_revoked")
            # session_keepalive (and anything unknown) only resets the wait timer

    async def _retire(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Close the superseded socket after a short grace so in-flight notifications still arrive."""
        await asyncio.sleep(self._retire_grace_s)
        with contextlib.suppress(Exception):
            await ws.close()
