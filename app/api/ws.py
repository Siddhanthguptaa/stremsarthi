"""``/api/ws``: fan-out of bus events to authenticated clients.

Protocol (docs/specs/app-api.md section 5.3):

* Client sends ``{"type":"auth","token":...}`` within ``ws_auth_timeout_s`` (else close 4401), then
  ``{"type":"subscribe","topics":[...]}``. The token never travels in the URL.
* Server frames: ``{"topic","seq","ts","data"}`` where ``data`` is ``contracts.wire.dumps(event)``
  verbatim; control frames ``{"type":"subscribed"|"error"|"ping"}``.
* ``seq`` is global per event (not per client), so a client can see that events were dropped.
* Per-client bounded queue (500): telemetry drops oldest, ``control.hold`` and ``engine.status`` are
  never dropped; a client more than ``ws_lag_close_s`` behind is closed with 4408.
* The socket is read-only apart from auth, subscribe and pong.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from contracts import (
    CONTRACT_VERSION,
    ContractError,
    EngineStatus,
    HoldCommand,
    HoldSource,
    Modality,
    Topic,
    dumps,
)
from contracts.enums import NEVER_DROP_TOPICS

from .auth import Principal, TokenService
from .bus import API_TOPICS, BusClient
from .settings import ApiSettings

log = logging.getLogger("streamsarthi.api.ws")
router = APIRouter()

CLOSE_UNAUTHORIZED = 4401
CLOSE_LAGGING = 4408
MAX_INBOUND_BYTES = 4096
# engine.status is as important as control.hold for the UI: both are never dropped.
KEEP_TOPICS = NEVER_DROP_TOPICS | {Topic.ENGINE_STATUS}
# The engine raises these itself; the API never publishes them and only reports them.
AUTO_HOLD_SOURCES = frozenset({HoldSource.AUTO_TRANSPORT, HoldSource.AUTO_EGRESS})

# Topic ACL by role (spec 5.3). The streamer must never receive moderator-queue text.
ROLE_TOPICS: dict[str, frozenset[Topic]] = {
    "streamer": frozenset(
        {
            Topic.SAFETY_EVENT,
            Topic.RISK,
            Topic.RISK_METER,
            Topic.CAPTION,
            Topic.CHAT_ALLOWED,
            Topic.CHAT_VERDICT,
            Topic.ENGINE_STATUS,
            Topic.HOLD,
        }
    ),
    "moderator": frozenset(
        {Topic.CHAT_MODQUEUE, Topic.CHAT_VERDICT, Topic.SAFETY_EVENT, Topic.HOLD, Topic.ENGINE_STATUS}
    ),
    "hotkey": frozenset(),  # REST hold only
    "viewer-readonly": frozenset(),  # report only
}
_MOD_SAFETY_MODALITIES = frozenset({Modality.CHAT, Modality.SYSTEM})


def event_visible_to(role: str, topic: Topic, event: Any) -> bool:
    """Per-event filter on top of the topic ACL."""
    if role == "moderator" and topic is Topic.SAFETY_EVENT:
        return event.modality in _MOD_SAFETY_MODALITIES
    return True


def _retrieve(task: asyncio.Task[Any]) -> None:
    """Done-callback: mark a connection task's exception as retrieved (no asyncio noise)."""
    if not task.cancelled():
        task.exception()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Client:
    def __init__(self, ws: WebSocket, principal: Principal, queue_max: int, lag_close_s: float) -> None:
        self.ws = ws
        self.principal = principal
        self.topics: frozenset[Topic] = frozenset()
        self.queue_max = queue_max
        self.lag_close_s = lag_close_s
        self.dq: deque[tuple[float, Topic, str]] = deque()
        self.wake = asyncio.Event()
        self.close_code: int | None = None
        self.missed_pings = 0
        self.dropped = 0
        self._send_lock = asyncio.Lock()

    def push(self, topic: Topic, frame: str, now: float) -> None:
        if len(self.dq) >= self.queue_max:
            for i, (_, t, _f) in enumerate(self.dq):
                if t not in KEEP_TOPICS:
                    del self.dq[i]
                    self.dropped += 1
                    break
        self.dq.append((now, topic, frame))
        self.check_lag(now)
        self.wake.set()

    def check_lag(self, now: float) -> None:
        if self.dq and now - self.dq[0][0] > self.lag_close_s and self.close_code is None:
            self.close_code = CLOSE_LAGGING
            self.wake.set()

    async def send(self, text: str) -> None:
        async with self._send_lock:
            await self.ws.send_text(text)

    async def send_json(self, obj: dict[str, Any]) -> None:
        await self.send(json.dumps(obj, separators=(",", ":")))


class EventHub:
    """Subscribes once to the bus and fans events out to every connected client."""

    def __init__(self, bus: BusClient, settings: ApiSettings, tokens: TokenService) -> None:
        self.bus = bus
        self.settings = settings
        self.tokens = tokens
        self.clients: set[Client] = set()
        self.seq = 0
        self.last_status: EngineStatus | None = None
        self._last_status_at = 0.0
        self.contract_ok = True
        self.hold_sources: set[HoldSource] = set()
        self._hold_seen = False  # a control.hold echo has been received since this process started
        self._task: asyncio.Task[None] | None = None
        self._sub: Any = None

    # --- lifecycle -------------------------------------------------------------------------
    async def start(self) -> None:
        self._sub = self.bus.subscribe(API_TOPICS)
        self._task = asyncio.create_task(self._pump(), name="ws-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._sub is not None:
            self._sub.close()
            self._sub = None

    # --- state -----------------------------------------------------------------------------
    def engine_status(self) -> EngineStatus | None:
        """The last ``engine.status`` unless it is older than ``engine_stale_s``."""
        if self.last_status is None or time.monotonic() - self._last_status_at > self.settings.engine_stale_s:
            return None
        return self.last_status

    def engine_age_s(self) -> float | None:
        """Seconds since the last ``engine.status`` (None if none was ever received)."""
        return None if self.last_status is None else time.monotonic() - self._last_status_at

    def hold_sources_now(self) -> list[HoldSource]:
        """Sources that hold the stream: the user sources seen on ``control.hold`` plus the
        engine's own automatic sources (transport or egress loss) from a fresh ``engine.status``."""
        sources = set(self.hold_sources)
        status = self.engine_status()
        if status is not None:
            sources |= {s for s in status.hold_sources if s in AUTO_HOLD_SOURCES}
        return sorted(sources, key=lambda s: s.value)

    def _track(self, topic: Topic, event: Any) -> None:
        if topic is Topic.ENGINE_STATUS:
            self.last_status = event
            self._last_status_at = time.monotonic()
            self.contract_ok = event.contract_version.split(".")[0] == CONTRACT_VERSION.split(".")[0]
            if not self._hold_seen:  # API restarted while the engine was holding: adopt its view
                self.hold_sources = {s for s in event.hold_sources if s not in AUTO_HOLD_SOURCES}
        elif topic is Topic.HOLD:
            self._hold_seen = True
            if event.active:
                self.hold_sources.add(event.source)
            else:
                self.hold_sources.discard(event.source)  # only clears its own source

    # --- fan-out ---------------------------------------------------------------------------
    async def _pump(self) -> None:
        async for topic, event in self._sub:
            try:
                self.dispatch(topic, event)
            except Exception:  # one bad event must not stop the relay
                log.exception("dispatch failed for %s", topic.value)

    def frame(self, topic: Topic, seq: int, ts: str, event: Any) -> str:
        data = dumps(event).decode("utf-8")  # already JSON: embedded verbatim
        return f'{{"topic":{json.dumps(topic.value)},"seq":{seq},"ts":"{ts}","data":{data}}}'

    def dispatch(self, topic: Topic, event: Any) -> None:
        self._track(topic, event)
        self.seq += 1
        if not self.clients:
            return
        ts = _now_iso()
        now = time.monotonic()
        try:
            text = self.frame(topic, self.seq, ts, event)
        except ContractError:
            log.warning("event on %s is not encodable; skipped", topic.value)
            return
        for client in tuple(self.clients):
            if topic in client.topics and event_visible_to(client.principal.role, topic, event):
                client.push(topic, text, now)

    def replay(self, client: Client) -> None:
        """Late joiners get the last engine status and the current hold state."""
        now = time.monotonic()
        ts = _now_iso()
        if Topic.ENGINE_STATUS in client.topics and self.last_status is not None:
            client.push(
                Topic.ENGINE_STATUS, self.frame(Topic.ENGINE_STATUS, self.seq, ts, self.last_status), now
            )
        if Topic.HOLD in client.topics:
            for source in sorted(self.hold_sources, key=lambda s: s.value):
                cmd = HoldCommand(source=source, active=True, actor="replay")
                client.push(Topic.HOLD, self.frame(Topic.HOLD, self.seq, ts, cmd), now)

    # --- connection ------------------------------------------------------------------------
    async def authenticate(self, ws: WebSocket) -> Principal | None:
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=self.settings.ws_auth_timeout_s)
            if len(raw) > MAX_INBOUND_BYTES:
                return None
            msg = json.loads(raw)
        except (TimeoutError, WebSocketDisconnect, ValueError):
            return None
        if not isinstance(msg, dict) or msg.get("type") != "auth":
            return None
        token = msg.get("token")
        if isinstance(token, str):
            principal = self.tokens.verify(token)
            if principal is not None:
                return principal
        if not self.settings.require_auth and not token:
            return Principal(role="streamer", room_id=self.tokens.room_id, exp=0, actor="dev")
        return None

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        principal = await self.authenticate(ws)
        if principal is None:
            await ws.close(code=CLOSE_UNAUTHORIZED)
            return
        client = Client(ws, principal, self.settings.ws_queue_max, self.settings.ws_lag_close_s)
        self.clients.add(client)
        tasks = [
            asyncio.create_task(self._reader(client)),
            asyncio.create_task(self._writer(client)),
            asyncio.create_task(self._pinger(client)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.clients.discard(client)
            # Cancel without awaiting: awaiting here can race a host-side cancellation (the test
            # client and ASGI servers cancel the handler right after a disconnect) and leak a
            # CancelledError. The tasks finish on the next loop turns; retrieve their errors.
            for t in tasks:
                t.cancel()
                t.add_done_callback(_retrieve)
            with contextlib.suppress(Exception):
                await ws.close(code=client.close_code or 1000)

    async def _reader(self, client: Client) -> None:
        role = client.principal.role
        allowed = ROLE_TOPICS.get(role, frozenset())
        while True:
            try:
                raw = await client.ws.receive_text()
            except WebSocketDisconnect:
                return
            if len(raw) > MAX_INBOUND_BYTES:
                client.close_code = 1009
                return
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "pong":
                client.missed_pings = 0
            elif kind == "subscribe":
                await self._subscribe(client, allowed, msg.get("topics"))
            # anything else is ignored: the socket is read-only

    async def _subscribe(self, client: Client, allowed: frozenset[Topic], topics: Any) -> None:
        if not isinstance(topics, list) or not all(isinstance(t, str) for t in topics):
            await client.send_json({"type": "error", "code": "invalid_subscribe"})
            return
        try:
            wanted = {Topic(t) for t in topics}
        except ValueError:
            await client.send_json({"type": "error", "code": "unknown_topic"})
            return
        denied = sorted(t.value for t in wanted if t not in allowed or t not in API_TOPICS)
        if denied:  # refused as a whole: nothing is subscribed
            await client.send_json({"type": "error", "code": "forbidden_topics", "topics": denied})
            return
        client.topics = frozenset(wanted)
        await client.send_json({"type": "subscribed", "topics": sorted(t.value for t in wanted)})
        self.replay(client)

    async def _writer(self, client: Client) -> None:
        while client.close_code is None:
            await client.wake.wait()
            client.wake.clear()
            while client.dq and client.close_code is None:
                _, _, text = client.dq.popleft()
                await client.send(text)

    async def _pinger(self, client: Client) -> None:
        interval = self.settings.ws_ping_interval_s
        next_ping = time.monotonic() + interval
        while True:
            now = time.monotonic()
            until_expiry = client.principal.exp - time.time() if client.principal.exp else float("inf")
            await asyncio.sleep(max(0.01, min(next_ping - now, until_expiry + 0.01)))
            now = time.monotonic()
            if client.principal.exp and time.time() >= client.principal.exp:
                client.close_code = CLOSE_UNAUTHORIZED  # token expired: the client re-authenticates
                return
            client.check_lag(now)
            if client.close_code is not None:
                return
            if now >= next_ping:
                if client.missed_pings >= 2:
                    client.close_code = CLOSE_LAGGING
                    return
                client.missed_pings += 1
                await client.send_json({"type": "ping"})
                next_ping = now + interval


@router.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.app.state.hub.serve(websocket)
