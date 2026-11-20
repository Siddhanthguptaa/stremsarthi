"""Process wiring for the chat shield (T4.5).

``build(config, bus)`` follows the ``module:build`` convention: it returns a ``ChatPipeline`` whose
classifier uses Groq when a key is configured (rules plus local fallback otherwise). Platform action
adapters are passed in by whoever owns the authorised connectors (``chat.youtube`` / ``chat.twitch``).
``make_youtube_actions`` builds ``YouTubeActions`` with ``allow_timeout`` taken from the config, so the
adapter and the dispatcher policy cannot disagree.
"""

from __future__ import annotations

from collections.abc import Callable
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
