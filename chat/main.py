"""Process wiring for the chat shield (T4.5).

``build(config, bus)`` follows the ``module:build`` convention: it returns a ``ChatPipeline`` whose
classifier uses Groq when a key is configured (rules plus local fallback otherwise). Platform action
adapters are passed in by whoever owns the authorised connectors (``chat.youtube`` / ``chat.twitch``).
``make_youtube_actions`` builds ``YouTubeActions`` with ``allow_timeout`` taken from the config, so the
adapter and the dispatcher policy cannot disagree.

``python -m chat.main`` is the live process (T6.1): it loads ``contracts.load_config``, opens the ZeroMQ bus
(``chat.zmq_bus``; endpoints from ``BUS_SUB_ENDPOINT`` / ``BUS_PUB_ENDPOINT``), runs ``chat.live.ChatService`` and
shuts down cleanly on SIGTERM / SIGINT. Exit codes: 0 ok or chat disabled, 1 config error, 2 no connector could
be built (missing credentials, no login), 3 bus error, 4 every connector failed for good. Flags: ``--login``
(interactive OAuth when no stored token), ``--remember`` (keep refresh tokens in the OS keyring), ``--profile``,
``--config-dir``, ``--env-file``, ``--log-level``. Logs are machine codes only; no secret or message text.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Sequence
from typing import Any

from chat.classify import GroqTransport, LlmTransport, groq_transport_from_env
from chat.common import Clock, Secret
from chat.moderation import PlatformActions
from chat.pipeline import ChatPipeline
from chat.youtube import IdMap, YouTubeActions, YouTubeClient
from contracts import Config, EventBus
from contracts.config import ChatConfig


def make_youtube_actions(
    chat: ChatConfig,
    client: YouTubeClient,
    *,
    get_live_chat_id: Callable[[], str | None],
    ids: IdMap | None = None,
    clock: Clock | None = None,
    on_status: Callable[[str], None] | None = None,
) -> YouTubeActions:
    return YouTubeActions(
        client,
        get_live_chat_id=get_live_chat_id,
        ids=ids,
        allow_timeout=chat.youtube.allow_timeout,
        clock=clock,
        on_status=on_status,
    )


def groq_from_config(config: Config) -> LlmTransport | None:
    key = config.groq_api_key.get_secret_value().strip() if config.groq_api_key else ""
    return GroqTransport(Secret(key)) if key else groq_transport_from_env()


def build(
    config: Config,
    bus: EventBus,
    *,
    youtube: PlatformActions | None = None,
    twitch: PlatformActions | None = None,
    llm: LlmTransport | None = None,
    **kwargs: Any,
) -> ChatPipeline:
    return ChatPipeline(
        bus,
        config.chat,
        llm=llm if llm is not None else groq_from_config(config),
        youtube=youtube,
        twitch=twitch,
        **kwargs,
    )


# ---------------------------------------------------------------------------- live process
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m chat.main", description="Stream Sarthi chat shield process")
    p.add_argument("--profile", choices=("dev", "demo"), default=None)
    p.add_argument("--config-dir", default="config/profiles")
    p.add_argument("--env-file", default=".env")
    p.add_argument(
        "--login", action="store_true", help="run the interactive OAuth flows when no token is stored"
    )
    p.add_argument(
        "--remember", action="store_true", help="keep refresh tokens in the OS keyring after login"
    )
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: Callable[[], None]) -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop)
        except (NotImplementedError, RuntimeError, ValueError):  # Windows loops, non-main threads
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop))


async def amain(
    argv: Sequence[str] | None = None,
    *,
    bus: EventBus | None = None,
    config: Config | None = None,
    options: Any | None = None,
    install_signals: bool = True,
) -> int:
    """Run the chat process; returns the exit code. ``bus``, ``config`` and ``options`` are test seams."""
    from chat.common import log_event
    from chat.live import ChatService, ExitCode, LiveOptions
    from chat.zmq_bus import BusError, ZmqBus, endpoints_from_env

    log = logging.getLogger("chat.main")
    args = _parser().parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if config is None:
        from contracts import load_config

        try:
            config = load_config(args.profile, args.config_dir, args.env_file)
        except Exception as exc:  # pydantic errors can echo values: report the class only
            log_event(log, "config_error", level=logging.ERROR, error=type(exc).__name__)
            return int(ExitCode.CONFIG_ERROR)
    live = options or LiveOptions(login=args.login, remember=args.remember)
    owned: ZmqBus | None = None
    if bus is None:
        sub_ep, pub_ep = endpoints_from_env()
        owned = ZmqBus(sub_ep, pub_ep, on_state=lambda code: log_event(log, code, level=logging.INFO))
        try:
            owned.start()
        except BusError:
            log_event(log, "bus_error", level=logging.ERROR)
            return int(ExitCode.BUS_ERROR)
        bus = owned
    service = ChatService(config, bus, live)
    if install_signals:
        _install_signal_handlers(asyncio.get_running_loop(), service.request_stop)
    try:
        return await service.run()
    finally:
        if owned is not None:
            owned.stop()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(amain(argv))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
