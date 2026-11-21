"""Live wiring of the chat process: connectors per config, pipeline, bus, supervision, clean shutdown.

``ChatService(config, bus, options).run()`` returns the process exit code (``ExitCode``):

* ``chat.enabled`` false                      -> OK (nothing to do, logged as ``chat_disabled``).
* no connector could be built (no credentials,
  no login, ``anonymous_read`` off)           -> NO_CONNECTORS. The reason of each platform is logged as a code.
* every connector failed for good (revoked
  consent, bad token)                         -> CONNECTOR_FAILED.
* otherwise it runs until ``request_stop()`` (SIGTERM / SIGINT) and returns OK. When the platform ends the chat
  (the stream is over) the connectors finish, and the process idles so the moderator queue keeps working.

Credentials come only from the validated ``Config`` (``.env``) and the OS keyring (opt-in ``remember``). They are
never printed or logged: every log line is a machine code (``chat.common.logsafe``). The only thing shown to the
owner is the OAuth consent URL / device code, and only in ``login`` mode.

A missing Groq key degrades to the local rules (``groq_key_missing``); the cascade already falls back to them.
A missing platform credential skips that platform and keeps the other one running.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import aiohttp

from chat.common import (
    ChatError,
    ChatPublisher,
    Connector,
    Secret,
    log_event,
)
from chat.common.bus import session_id_from_env
from chat.main import build, groq_from_config, make_youtube_actions
from chat.pipeline import ChatPipeline
from chat.twitch import (
    ChatEvents,
    DeviceAuth,
    DevicePrompt,
    EventSubTransport,
    InvalidToken,
    Tokens,
    TokenStore,
    TwitchActions,
    UserSession,
)
from chat.twitch import make_http as make_twitch_http
from chat.twitch.irc import AnonymousIrcReader
from chat.youtube import (
    IdMap,
    QuotaGovernor,
    YouTubeAuth,
    YouTubeClient,
    YouTubePoller,
    YouTubeSession,
    YouTubeTokens,
    YouTubeTokenStore,
)
from chat.youtube import make_http as make_youtube_http
from contracts import Config, EventBus

_LOG = logging.getLogger("chat.live")
_RESTART_AFTER_S = 5.0
_STOP_GRACE_S = 5.0
_YT_RESTART_USED_FRACTION = 0.25  # a restart cannot know today's spend: assume the worst (spec 5.2)

YouTubeTokenProvider = Callable[[YouTubeAuth], Awaitable[YouTubeTokens | None]]
TwitchTokenProvider = Callable[[DeviceAuth], Awaitable[Tokens | None]]


class ExitCode(IntEnum):
    OK = 0
    CONFIG_ERROR = 1
    NO_CONNECTORS = 2
    BUS_ERROR = 3
    CONNECTOR_FAILED = 4


@dataclass
class LiveOptions:
    """Run-time switches. The ``*_origin`` / ``twitch_ws_url`` fields point the clients at local fakes (tests)."""

    login: bool = False  # run the interactive OAuth flows when no stored token exists
    remember: bool = False  # keep refresh tokens in the OS keyring after a login
    youtube_tokens: YouTubeTokenProvider | None = None
    twitch_tokens: TwitchTokenProvider | None = None
    youtube_origin: str | None = None
    twitch_origin: str | None = None
    twitch_ws_url: str | None = None
    youtube_auth_url: str | None = None
    twitch_mock_ws: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _present_url(url: str) -> None:
    print("Open this URL in your browser to authorise Stream Sarthi (YouTube):", file=sys.stderr)  # noqa: T201
    print(url, file=sys.stderr)  # noqa: T201


def _present_device(prompt: DevicePrompt) -> None:
    print(  # noqa: T201
        f"Open {prompt.uri.reveal()} and enter the code {prompt.code.reveal()} (Twitch).", file=sys.stderr
    )


@dataclass
class _Platform:
    name: str
    connector: Connector
    task: asyncio.Task[None] | None = None


class ChatService:
    def __init__(self, config: Config, bus: EventBus, options: LiveOptions | None = None) -> None:
        self.config = config
        self.bus = bus
        self.options = options or LiveOptions()
        self.session_id = session_id_from_env()
        self.publisher = ChatPublisher(bus, session_id=self.session_id)
        self.pipeline: ChatPipeline | None = None
        self.platforms: list[_Platform] = []
        self.skipped: dict[str, str] = {}  # platform -> reason code
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._aio: aiohttp.ClientSession | None = None

    # ---- public -------------------------------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    async def wait_ready(self, timeout_s: float = 5.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout_s)

    async def run(self) -> int:
        chat = self.config.chat
        if not chat.enabled:
            log_event(_LOG, "chat_disabled")
            return int(ExitCode.OK)
        if groq_from_config(self.config) is None:
            log_event(_LOG, "groq_key_missing", level=logging.WARNING, mode="rules_only")
        async with aiohttp.ClientSession() as aio:
            self._aio = aio
            yt = await self._setup_youtube(aio) if "youtube" in chat.platforms else None
            tw = await self._setup_twitch(aio) if "twitch" in chat.platforms else None
            if yt is None and tw is None:
                log_event(_LOG, "chat_no_connectors", level=logging.ERROR)
                return int(ExitCode.NO_CONNECTORS)
            return await self._serve(yt, tw)

    # ---- serve and shutdown ---------------------------------------------------------------------
    async def _serve(self, yt: _Setup | None, tw: _Setup | None) -> int:
        pipeline = build(
            self.config,
            self.bus,
            youtube=yt.actions if yt else None,
            twitch=tw.actions if tw else None,
            session_id=self.session_id,
        )
        self.pipeline = pipeline
        pipeline.start()
        try:
            for setup in (yt, tw):
                if setup is not None:
                    platform = setup.make_connector(pipeline)
                    platform.task = asyncio.get_running_loop().create_task(
                        self._supervise(platform), name=f"chat-{platform.name}"
                    )
                    self.platforms.append(platform)
            log_event(_LOG, "chat_started", connectors=len(self.platforms))
            self._ready.set()
            return await self._wait()
        finally:
            await self._shutdown(pipeline)

    async def _wait(self) -> int:
        stopper = asyncio.ensure_future(self._stop.wait())
        try:
            while not self._stop.is_set():
                tasks = {p.task for p in self.platforms if p.task is not None and not p.task.done()}
                if not tasks:
                    if all(p.connector.state == "failed" for p in self.platforms):
                        log_event(_LOG, "chat_all_connectors_failed", level=logging.ERROR)
                        return int(ExitCode.CONNECTOR_FAILED)
                    await stopper  # the chats ended normally: idle so the mod queue keeps working
                    break
                await asyncio.wait({stopper, *tasks}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stopper.cancel()
        return int(ExitCode.OK)

    async def _supervise(self, platform: _Platform) -> None:
        """``Connector.run`` already reconnects with backoff; this only guards against an escaped bug."""
        while not self._stop.is_set():
            try:
                await platform.connector.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never log the message: only the class name
                log_event(
                    _LOG, "connector_crashed", level=logging.ERROR, platform=platform.name,
                    error=type(exc).__name__,
                )  # fmt: skip
                with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(self._stop.wait(), _RESTART_AFTER_S)

    async def _shutdown(self, pipeline: ChatPipeline) -> None:
        for platform in self.platforms:
            with contextlib.suppress(Exception):
                await platform.connector.stop()
        tasks = [p.task for p in self.platforms if p.task is not None]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_STOP_GRACE_S)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await pipeline.idle(2.0)  # let queued messages and platform actions finish
        with contextlib.suppress(Exception):
            await pipeline.stop()
        log_event(_LOG, "chat_stopped")

    # ---- YouTube --------------------------------------------------------------------------------
    async def _setup_youtube(self, aio: aiohttp.ClientSession) -> _Setup | None:
        cfg = self.config
        client_id = (
            cfg.youtube_oauth_client_id.get_secret_value().strip() if cfg.youtube_oauth_client_id else ""
        )
        if not client_id:
            return self._skip("youtube", "yt_no_credentials")
        secret_raw = (
            cfg.youtube_oauth_client_secret.get_secret_value().strip()
            if cfg.youtube_oauth_client_secret
            else ""
        )
        opts = self.options
        http = make_youtube_http(aio, loopback_origin=opts.youtube_origin)
        kwargs: dict[str, Any] = {}
        if opts.youtube_auth_url:
            kwargs["auth_url"] = opts.youtube_auth_url
        auth = YouTubeAuth(client_id, Secret(secret_raw) if secret_raw else None, http, **kwargs)
        try:
            tokens = await (opts.youtube_tokens or self._default_youtube_tokens)(auth)
        except ChatError as exc:
            return self._skip("youtube", exc.code)
        if tokens is None:
            return self._skip("youtube", "yt_login_required")
        chat = cfg.chat
        sink = self.publisher.status_sink("youtube")
        governor = QuotaGovernor(
            daily_units=chat.youtube.daily_quota_units,
            action_budget_fraction=chat.youtube.action_budget_fraction,
            planned_stream_hours=chat.youtube.planned_stream_hours,
            min_poll_s=chat.youtube.min_poll_s,
            initial_used_fraction=_YT_RESTART_USED_FRACTION,
            on_event=sink,
        )
        client = YouTubeClient(http, YouTubeSession(auth, tokens), governor)
        ids = IdMap()
        holder: dict[str, YouTubePoller] = {}
        actions = make_youtube_actions(
            chat,
            client,
            get_live_chat_id=lambda: holder["poller"].live_chat_id if "poller" in holder else None,
            ids=ids,
            on_status=sink,
        )

        def make(pipeline: ChatPipeline) -> _Platform:
            poller = YouTubePoller(
                client, pipeline.on_message, ids=ids, min_poll_s=chat.youtube.min_poll_s, on_status=sink
            )
            holder["poller"] = poller
            return _Platform("youtube", poller)

        return _Setup(actions, make)

    async def _default_youtube_tokens(self, auth: YouTubeAuth) -> YouTubeTokens | None:
        store: YouTubeTokenStore | None = None
        try:
            store = YouTubeTokenStore()
        except ChatError:
            log_event(_LOG, "keyring_unavailable", level=logging.INFO, platform="youtube")
        if store is not None:
            try:
                stored = store.load(auth.client_id)
            except ChatError as exc:
                log_event(_LOG, exc.code, level=logging.WARNING, platform="youtube")
                stored = None
            if stored is not None:
                return stored
        if not self.options.login:
            return None
        tokens = await auth.login(_present_url)
        if store is not None and self.options.remember:
            with contextlib.suppress(ChatError):
                store.save(auth.client_id, tokens, remember=True)
        return tokens

    # ---- Twitch ---------------------------------------------------------------------------------
    async def _setup_twitch(self, aio: aiohttp.ClientSession) -> _Setup | None:
        cfg = self.config
        tcfg = cfg.chat.twitch
        sink = self.publisher.status_sink("twitch")
        opts = self.options
        if tcfg.anonymous_read:  # dev without OAuth: read only, no actions
            login = (tcfg.channel_login or "").strip()
            if not login:
                return self._skip("twitch", "twitch_no_channel")

            def make_irc(pipeline: ChatPipeline) -> _Platform:
                kwargs: dict[str, Any] = {}
                if opts.twitch_ws_url:
                    kwargs.update(url=opts.twitch_ws_url, mock=opts.twitch_mock_ws)
                reader = AnonymousIrcReader(login, pipeline.on_message, aio, on_status=sink, **kwargs)
                return _Platform("twitch", reader)

            return _Setup(None, make_irc)

        client_id = cfg.twitch_client_id.get_secret_value().strip() if cfg.twitch_client_id else ""
        if not client_id:
            return self._skip("twitch", "twitch_no_credentials")
        http = make_twitch_http(aio, loopback_origin=opts.twitch_origin)
        auth = DeviceAuth(client_id, http)
        try:
            tokens = await (opts.twitch_tokens or self._default_twitch_tokens)(auth)
        except ChatError as exc:
            return self._skip("twitch", exc.code)
        if tokens is None:
            return self._skip("twitch", "twitch_login_required")
        session = UserSession(auth, tokens)
        try:
            await session.access_token()  # validates the token and learns the user id
            broadcaster_id = await self._resolve_broadcaster(http, session, client_id, tcfg.channel_login)
        except InvalidToken:
            return self._skip("twitch", "twitch_auth_failed")
        except ChatError as exc:
            return self._skip("twitch", exc.code)
        actions = TwitchActions(http, session, broadcaster_id, on_status=sink)

        def make(pipeline: ChatPipeline) -> _Platform:
            events = ChatEvents(broadcaster_id, pipeline.submit, sink, mock=opts.twitch_mock_ws)
            kwargs: dict[str, Any] = {}
            if opts.twitch_ws_url:
                kwargs.update(url=opts.twitch_ws_url, mock=opts.twitch_mock_ws)
            transport = EventSubTransport(
                events, session, http, aio, broadcaster_id=broadcaster_id, on_status=sink, **kwargs
            )
            return _Platform("twitch", transport)

        return _Setup(actions, make)

    @staticmethod
    async def _resolve_broadcaster(
        http: Any, session: UserSession, client_id: str, channel_login: str | None
    ) -> str:
        user_id = session.user_id
        if user_id is None:
            raise ChatError("twitch_validation_failed")
        login = (channel_login or "").strip().lower().lstrip("#")
        if not login:
            return user_id  # the authorised account is the streamer
        token = await session.access_token()
        response = await http.request(
            "GET",
            "/helix/users",
            params={"login": login},
            headers={"Client-Id": client_id, "Authorization": "Bearer " + token.reveal()},
        )
        rows = response.body.get("data")
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
        found = row.get("id") if isinstance(row, dict) else None
        if response.status != 200 or not isinstance(found, str) or not found:
            raise ChatError("twitch_channel_unresolved")
        return found

    async def _default_twitch_tokens(self, auth: DeviceAuth) -> Tokens | None:
        store: TokenStore | None = None
        try:
            store = TokenStore()
        except ChatError:
            log_event(_LOG, "keyring_unavailable", level=logging.INFO, platform="twitch")
        service = "stream-sarthi-twitch:" + auth.client_id
        if store is not None:
            try:
                last = store.backend.get_password(service, "_last_user")
                stored = store.load(auth.client_id, last) if last else None
            except Exception as exc:  # code only: the keyring may carry anything
                log_event(_LOG, "keyring_read_failed", level=logging.WARNING, error=type(exc).__name__)
                stored = None
            if stored is not None:
                return stored
        if not self.options.login:
            return None
        tokens = await auth.login(_present_device, asyncio.Event())
        if store is not None and self.options.remember:
            session = UserSession(auth, tokens)
            with contextlib.suppress(Exception):
                await session.access_token()
                if session.user_id:
                    store.save(auth.client_id, session.user_id, session.tokens, remember=True)
                    store.backend.set_password(service, "_last_user", session.user_id)
        return tokens

    # ---- helpers --------------------------------------------------------------------------------
    def _skip(self, platform: str, code: str) -> None:
        self.skipped[platform] = code
        log_event(_LOG, "connector_skipped", level=logging.WARNING, platform=platform, code=code)
        return None


@dataclass
class _Setup:
    actions: Any
    make_connector: Callable[[ChatPipeline], _Platform]
