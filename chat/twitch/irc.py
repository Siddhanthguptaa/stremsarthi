"""Anonymous read-only Twitch chat over IRC-on-WebSocket (dev without OAuth, spec 5.3).

Enabled by ``chat.twitch.anonymous_read=true``. Logs in as ``justinfan<digits>`` (Twitch's documented
anonymous nick), requests tags so badges are available, joins one channel and yields ``ChatMessage``.
It can read only: there is no token, so it has no moderation actions (the dispatcher reports
``unsupported`` and flagged messages are held for a moderator instead). Roles still come from the
``badges`` tag, never from text. A dropped connection reports ``twitch_disconnected`` once and
reconnects with backoff; it never asserts a hold.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp

from chat.common.connector import Connector, FatalConnectorError, RetryableError
from chat.common.logsafe import hash_id, log_event
from contracts import ChatMessage

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
_LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")
_MAX_LINE = 8192
_TAG_UNESCAPE = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}
_LOG = logging.getLogger("chat.twitch.irc")
OnMessage = Callable[[ChatMessage], Awaitable[None] | None]


def irc_url(url: str, *, mock: bool = False) -> str:
    parts = urlsplit(url)
    if parts.username or parts.password or parts.fragment:
        raise ValueError("unapproved IRC endpoint")
    if mock:
        ok = parts.scheme == "ws" and parts.hostname == "127.0.0.1" and parts.port is not None
    else:
        ok = parts.scheme == "wss" and parts.hostname == "irc-ws.chat.twitch.tv"
    if not ok:
        raise ValueError("unapproved IRC endpoint")
    return url


def _unescape(value: str) -> str:
    out: list[str] = []
    it = iter(value)
    for ch in it:
        if ch == "\\":
            nxt = next(it, "")
            out.append(_TAG_UNESCAPE.get(nxt, nxt))
        else:
            out.append(ch)
    return "".join(out)


def parse_line(line: str) -> tuple[dict[str, str], str, list[str]]:
    """Split one IRC line into ``(tags, command, params)``. The trailing parameter is last."""
    tags: dict[str, str] = {}
    rest = line
    if rest.startswith("@"):
        raw, _, rest = rest[1:].partition(" ")
        for item in raw.split(";"):
            key, _, val = item.partition("=")
            tags[key] = _unescape(val)
    rest = rest.lstrip(" ")
    if rest.startswith(":"):
        _, _, rest = rest.partition(" ")
    trailing: str | None = None
    if " :" in rest:
        rest, _, trailing = rest.partition(" :")
    elif rest.startswith(":"):
        trailing, rest = rest[1:], ""
    parts = rest.split()
    command = parts[0].upper() if parts else ""
    params = parts[1:]
    if trailing is not None:
        params.append(trailing)
    return tags, command, params


class AnonymousIrcReader(Connector):
    def __init__(
        self,
        channel_login: str,
        on_message: OnMessage,
        ws_session: aiohttp.ClientSession,
        *,
        url: str = IRC_URL,
        mock: bool = False,
        read_timeout_s: float = 420.0,
        seen_capacity: int = 4096,
        **connector_kwargs: object,
    ) -> None:
        super().__init__("twitch_irc", **connector_kwargs)  # type: ignore[arg-type]
        login = channel_login.strip().lower().lstrip("#")
        if not _LOGIN_RE.match(login):
            raise ValueError("invalid Twitch channel login")
        self.channel = login
        self.url = irc_url(url, mock=mock)
        self.on_message = on_message
        self._ws_session = ws_session
        self._read_timeout_s = read_timeout_s
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._seen_capacity = seen_capacity
        self._outage_reported = False
        self.delivered = 0

    async def _run_session(self) -> None:
        try:
            await self._session()
        except asyncio.CancelledError:
            raise
        except BaseException:
            if not self._outage_reported and not self.stopped:
                self._outage_reported = True
                self.emit_status("twitch_disconnected")
            raise

    async def _session(self) -> None:
        try:
            ws = await self._ws_session.ws_connect(self.url, heartbeat=None, max_msg_size=_MAX_LINE * 4)
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise RetryableError("twitch_irc_connect_failed") from exc
        async with ws:
            nick = f"justinfan{secrets.randbelow(80000) + 10000}"
            for line in (
                "CAP REQ :twitch.tv/tags twitch.tv/commands",
                "PASS SCHMOOPIIE",
                f"NICK {nick}",
                f"JOIN #{self.channel}",
            ):
                await ws.send_str(line)
            while True:
                try:
                    frame = await asyncio.wait_for(ws.receive(), self._read_timeout_s)
                except TimeoutError as exc:
                    raise RetryableError("twitch_irc_silent") from exc
                if frame.type != aiohttp.WSMsgType.TEXT:
                    raise RetryableError("twitch_irc_closed")
                for line in str(frame.data).split("\r\n"):
                    if line:
                        await self._on_line(ws, line)

    async def _on_line(self, ws: aiohttp.ClientWebSocketResponse, line: str) -> None:
        if len(line) > _MAX_LINE:
            return
        tags, command, params = parse_line(line)
        if command == "PING":
            await ws.send_str("PONG :" + (params[-1] if params else "tmi.twitch.tv"))
        elif command == "RECONNECT":
            raise RetryableError("twitch_irc_reconnect")
        elif command == "001":
            self.mark_healthy()
            if self._outage_reported:
                self._outage_reported = False
                self.emit_status("twitch_reconnected")
        elif command == "NOTICE" and tags.get("msg-id") in ("msg_channel_suspended", "msg_banned"):
            raise FatalConnectorError("twitch_irc_channel_unavailable")
        elif command == "PRIVMSG" and len(params) >= 2:
            await self._deliver(tags, params[-1])

    async def _deliver(self, tags: dict[str, str], text: str) -> None:
        message_id = tags.get("id", "")
        author_id = tags.get("user-id", "")
        room = tags.get("room-id", "")
        if not (message_id and author_id and room):
            return
        if message_id in self._seen:
            return
        self._seen[message_id] = None
        while len(self._seen) > self._seen_capacity:
            self._seen.popitem(last=False)
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):
            text = text[8:-1]
        badges = {b.partition("/")[0] for b in tags.get("badges", "").split(",") if b}
        try:
            message = ChatMessage(
                platform="twitch",
                message_id=message_id,
                channel_id=room,
                author_id=author_id,
                author_display=tags.get("display-name", ""),
                text=text,
                received_at=self.clock.wall(),
                is_broadcaster="broadcaster" in badges or author_id == room,
                is_moderator="moderator" in badges,
            )
        except ValueError:
            return
        try:
            result = self.on_message(message)
            if asyncio.iscoroutine(result):
                await result
            self.delivered += 1
        except Exception as exc:
            log_event(
                _LOG,
                "deliver_failed",
                level=logging.WARNING,
                mid=hash_id(message_id),
                error=type(exc).__name__,
            )
