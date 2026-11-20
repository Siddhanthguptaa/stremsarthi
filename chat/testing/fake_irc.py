"""Loopback fake of Twitch IRC-over-WebSocket for the anonymous reader tests. Synthetic data only."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from aiohttp import WSMsgType, web


@dataclass
class FakeIrc:
    received: list[str] = field(default_factory=list)  # lines the client sent
    connections: int = 0
    script: list[list[str]] = field(default_factory=list)  # per connection: lines to push after JOIN
    origin: str = ""
    _runner: web.AppRunner | None = None
    _open: list[web.WebSocketResponse] = field(default_factory=list)

    @property
    def url(self) -> str:
        return self.origin.replace("http://", "ws://") + "/"

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # noqa: SLF001
        self.origin = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        for ws in list(self._open):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    async def push(self, *lines: str) -> None:
        for ws in self._open:
            if not ws.closed:
                await ws.send_str("\r\n".join(lines) + "\r\n")

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        index = self.connections
        self.connections += 1
        self._open.append(ws)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            self.received.append(msg.data)
            if msg.data.startswith("JOIN "):
                await ws.send_str(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!\r\n")
                if index < len(self.script):
                    lines = [x for x in self.script[index] if x != "<close>"]
                    if lines:
                        await ws.send_str("\r\n".join(lines) + "\r\n")
                    if "<close>" in self.script[index]:
                        await ws.close()
        return ws


def privmsg(
    msg_id: str, user_id: str, text: str, *, room: str = "1001", badges: str = "", name: str = "Viewer"
) -> str:
    tags = f"badges={badges};display-name={name};id={msg_id};room-id={room};user-id={user_id}"
    return f"@{tags} :{name.lower()}!{name.lower()}@{name.lower()}.tmi.twitch.tv PRIVMSG #chan :{text}"
