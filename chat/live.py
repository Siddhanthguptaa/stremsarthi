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
import os
import sys
import threading
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
from chat.tokenfile import default_backend
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
    LOGIN_FAILED = 5


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


YT_LOOPBACK_PORT_ENV = "CHAT_YT_LOGIN_PORT"
YT_LOOPBACK_PORT_DEFAULT = 8765


def youtube_login_port() -> int:
    """Fixed loopback port of the YouTube consent redirect (WSL2 forwards it to the Windows browser)."""
    raw = os.environ.get(YT_LOOPBACK_PORT_ENV, "").strip()
    try:
        port = int(raw) if raw else YT_LOOPBACK_PORT_DEFAULT
    except ValueError:
        return YT_LOOPBACK_PORT_DEFAULT
    return port if 1024 <= port <= 65535 else YT_LOOPBACK_PORT_DEFAULT


def _present_url(url: str) -> None:
    # The consent URL carries a one-time state and PKCE challenge, no token. It is meant to be opened.
    print(  # noqa: T201
        "\nYOUTUBE LOGIN: open this URL in your Windows browser and approve. After approval the browser is "
        "sent back to http://127.0.0.1:<port>/callback and this window finishes by itself.\n"
        "If the browser shows 'cannot connect', copy the full address from its address bar and paste it "
        "here, then press Enter.\n",
        file=sys.stderr,
    )
    print(url, file=sys.stderr)  # noqa: T201


def _present_device(prompt: DevicePrompt) -> None:
    print(  # noqa: T201
        f"\nTWITCH LOGIN: open {prompt.uri.reveal()} in your Windows browser, enter the code "
        f"{prompt.code.reveal()} and approve. This window finishes by itself.\n",
        file=sys.stderr,
    )


def _watch_stdin_for_redirect(loop: asyncio.AbstractEventLoop, receiver: Any) -> None:
    """Daemon thread: a pasted redirect URL completes the login when the browser cannot reach the port."""
    if not sys.stdin or not sys.stdin.isatty():
        return

    def reader() -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                return
            if not line:
                return
            loop.call_soon_threadsafe(receiver.submit_redirect, line)

    threading.Thread(target=reader, name="chat-login-stdin", daemon=True).start()


async def login_youtube(
    auth: YouTubeAuth, store: YouTubeTokenStore, *, remember: bool = True, strict: bool = True
) -> YouTubeTokens:
    loop = asyncio.get_running_loop()
    tokens = await auth.login(
        _present_url,
        port=youtube_login_port(),
        on_receiver=lambda receiver: _watch_stdin_for_redirect(loop, receiver),
    )
    if remember:
        try:
            store.save(auth.client_id, tokens, remember=True)
        except ChatError:
            if strict:
                raise
            log_event(_LOG, "token_save_failed", level=logging.WARNING, platform="youtube")
    return tokens


async def login_twitch(
    auth: DeviceAuth, store: TokenStore, *, remember: bool = True, strict: bool = True
) -> Tokens:
    tokens = await auth.login(_present_device, asyncio.Event())
    if remember:
        try:
            session = UserSession(auth, tokens)
            await session.access_token()  # validates the token and learns the user id
            if session.user_id:
                store.save(auth.client_id, session.user_id, session.tokens, remember=True)
                store.backend.set_password(
                    "stream-sarthi-twitch:" + auth.client_id, "_last_user", session.user_id
                )
        except Exception as exc:
            if strict:
                raise
            log_event(_LOG, "token_save_failed", level=logging.WARNING, error=type(exc).__name__)
    return tokens


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
        self.announce_gap_s = 0.4

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
                await self.announce_unavailable()
                return int(ExitCode.NO_CONNECTORS)
            if self.skipped:  # one platform is up, the other is not: say so
                await self.announce_unavailable(repeats=1)
            code = await self._serve(yt, tw)
            if code == int(ExitCode.CONNECTOR_FAILED):
                await self.announce_unavailable(extra="chat_connectors_failed")
            return code

    async def announce_unavailable(self, *, repeats: int = 3, extra: str = "chat_unavailable") -> None:
        """Tell the moderator console chat is not running (SYSTEM_STATUS safety events, codes only).

        Published a few times because a freshly started process can lose its first messages on the bus
        (PUB/SUB slow joiner). Never raises: the stack must not depend on chat."""
        for i in range(repeats):
            with contextlib.suppress(Exception):
                self.publisher.safety(extra, source="chat")
                for platform, reason in self.skipped.items():
                    self.publisher.safety(reason, source=f"chat.{platform}")
            if i + 1 < repeats:
                await asyncio.sleep(self.announce_gap_s)

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
        store = YouTubeTokenStore(default_backend())  # keyring when real, else the 0600 token file
        try:
            stored = store.load(auth.client_id)
        except ChatError as exc:
            log_event(_LOG, exc.code, level=logging.WARNING, platform="youtube")
            stored = None
        if stored is not None:
            return stored
        if not self.options.login:
            return None
        return await login_youtube(auth, store, remember=self.options.remember, strict=False)

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
        store = TokenStore(default_backend())  # keyring when real, else the 0600 token file
        service = "stream-sarthi-twitch:" + auth.client_id
        try:
            last = store.backend.get_password(service, "_last_user")
            stored = store.load(auth.client_id, last) if last else None
        except Exception as exc:  # code only: the store may carry anything
            log_event(_LOG, "keyring_read_failed", level=logging.WARNING, error=type(exc).__name__)
            stored = None
        if stored is not None:
            return stored
        if not self.options.login:
            return None
        return await login_twitch(auth, store, remember=self.options.remember, strict=False)

    # ---- helpers --------------------------------------------------------------------------------
    def _skip(self, platform: str, code: str) -> None:
        self.skipped[platform] = code
        log_event(_LOG, "connector_skipped", level=logging.WARNING, platform=platform, code=code)
        return None


async def run_login(config: Config, *, remember: bool = True) -> int:
    """Standalone ``python -m chat.main --login``: log in to every configured platform, store, exit.

    Needs neither the engine nor the bus. Exit 0 all done, 2 nothing to log in to (no client id), 5 a login failed."""
    chat = config.chat
    done = failed = 0
    async with aiohttp.ClientSession() as aio:
        if "youtube" in chat.platforms:
            cid = config.youtube_oauth_client_id.get_secret_value().strip() if config.youtube_oauth_client_id else ""
            if not cid:
                log_event(_LOG, "login_skipped", level=logging.WARNING, platform="youtube", code="yt_no_credentials")
            else:
                sec = (
                    config.youtube_oauth_client_secret.get_secret_value().strip()
                    if config.youtube_oauth_client_secret
                    else ""
                )
                auth = YouTubeAuth(cid, Secret(sec) if sec else None, make_youtube_http(aio))
                try:
                    await login_youtube(auth, YouTubeTokenStore(default_backend()), remember=remember)
                    log_event(_LOG, "login_ok", platform="youtube")
                    done += 1
                except ChatError as exc:
                    log_event(_LOG, "login_failed", level=logging.ERROR, platform="youtube", code=exc.code)
                    failed += 1
        if "twitch" in chat.platforms and not chat.twitch.anonymous_read:
            cid = config.twitch_client_id.get_secret_value().strip() if config.twitch_client_id else ""
            if not cid:
                log_event(_LOG, "login_skipped", level=logging.WARNING, platform="twitch", code="twitch_no_credentials")
            else:
                tauth = DeviceAuth(cid, make_twitch_http(aio))
                try:
                    await login_twitch(tauth, TokenStore(default_backend()), remember=remember)
                    log_event(_LOG, "login_ok", platform="twitch")
                    done += 1
                except ChatError as exc:
                    log_event(_LOG, "login_failed", level=logging.ERROR, platform="twitch", code=exc.code)
                    failed += 1
                except Exception as exc:  # InvalidToken and friends: class only
                    log_event(_LOG, "login_failed", level=logging.ERROR, platform="twitch", code=type(exc).__name__)
                    failed += 1
    if failed:
        return int(ExitCode.LOGIN_FAILED)
    return int(ExitCode.OK) if done else int(ExitCode.NO_CONNECTORS)


@dataclass
class _Setup:
    actions: Any
    make_connector: Callable[[ChatPipeline], _Platform]
