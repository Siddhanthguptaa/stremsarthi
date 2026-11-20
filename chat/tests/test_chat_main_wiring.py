"""Process wiring: YouTubeActions gets allow_timeout from the config; build() returns a pipeline."""

from __future__ import annotations

from types import SimpleNamespace

from chat.main import build, make_youtube_actions
from chat.pipeline import ChatPipeline
from contracts import Config
from contracts.config import ChatConfig, YoutubeChatConfig
from contracts.fakes import InProcessBus


def test_make_youtube_actions_mirrors_allow_timeout(yt: SimpleNamespace) -> None:
    off = make_youtube_actions(ChatConfig(), yt.client, get_live_chat_id=lambda: "chat1")
    on = make_youtube_actions(
        ChatConfig(youtube=YoutubeChatConfig(allow_timeout=True)), yt.client, get_live_chat_id=lambda: "chat1"
    )
    assert off.allow_timeout is False and on.allow_timeout is True


def test_build_returns_pipeline_without_groq_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = Config(groq_api_key=None)
    pipe = build(cfg, InProcessBus())
    assert isinstance(pipe, ChatPipeline) and pipe.classifier.llm_stage is None
