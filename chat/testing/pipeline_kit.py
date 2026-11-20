"""Test rig for the chat pipeline: in-process bus, scriptable platform adapters, message factory."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from chat.classify.llm import LlmTransport
from chat.common import ActionResult, ManualClock
from chat.pipeline import ChatPipeline
from contracts import ChatMessage, ModAction, Topic
from contracts.config import ChatConfig
from contracts.fakes import InProcessBus

# Obvious SPECIMEN triggers (the same kind the classifier tests use).
ABUSE = "tu chutiya harami kutta"  # rules, 0.9+: HIDE_FROM_STREAMER
SEVERE = "madarchod"  # severity 3: TIMEOUT by default
FAKE_UPI = "upi://pay?pa=specimen@ybl&am=500"  # DELETE
PHONE = "call 9876501234"  # doxxing: DELETE
BORDERLINE = "bc what a play"  # 0.3 <= score < 0.9: provisional, LLM decides
CLEAN = "bhai kya gameplay hai"


class Stub:
    """A platform adapter that records calls and returns scripted results."""

    def __init__(self, delete: ActionResult | None = None, timeout: ActionResult | None = None) -> None:
        self.delete_result = delete or ActionResult("done", "ok")
        self.timeout_result = timeout or ActionResult("done", "ok")
        self.calls: list[tuple[str, str]] = []
        self.allow_timeout = False
        self.gate: asyncio.Event | None = None

    async def delete(self, message: ChatMessage) -> ActionResult:
        self.calls.append(("delete", message.message_id))
        if self.gate is not None:
            await self.gate.wait()
        return self.delete_result

    async def timeout(self, message: ChatMessage, seconds: int) -> ActionResult:
        self.calls.append(("timeout", f"{message.message_id}:{seconds}"))
        return self.timeout_result


def msg(
    text: str,
    i: int = 1,
    platform: str = "twitch",
    *,
    author: str | None = None,
    broadcaster: bool = False,
    moderator: bool = False,
) -> ChatMessage:
    return ChatMessage(
        platform=platform,  # type: ignore[arg-type]
        message_id=f"m{i}",
        channel_id="1001",
        author_id=author or f"author{i}",
        author_display="viewer",
        text=text,
        received_at=1_700_000_000.0,
        is_broadcaster=broadcaster,
        is_moderator=moderator,
    )


def mod_action(
    i: int, action: str = "approve", platform: str = "twitch", seconds: int | None = None
) -> ModAction:
    return ModAction(f"m{i}", platform, action, seconds, "actorhash0001")  # type: ignore[arg-type]


@dataclass
class Rig:
    bus: InProcessBus
    pipe: ChatPipeline
    twitch: Stub
    youtube: Stub
    clock: Any
    extra: dict[str, Any] = field(default_factory=dict)

    def of(self, topic: Topic) -> list[Any]:
        return self.bus.of(topic)

    def allowed_ids(self) -> list[str]:
        return [m.message_id for m in self.of(Topic.CHAT_ALLOWED)]

    def verdicts(self, mid: str) -> list[Any]:
        return [v for v in self.of(Topic.CHAT_VERDICT) if v.message_id == mid]

    def queue_item(self, mid: str) -> Any:
        items = [i for i in self.of(Topic.CHAT_MODQUEUE) if i.message.message_id == mid]
        return items[-1] if items else None

    def safety(self, code: str | None = None) -> list[Any]:
        events = self.of(Topic.SAFETY_EVENT)
        return [e for e in events if code is None or e.detail_code == code]

    async def settle(self, timeout_s: float = 5.0) -> None:
        """Queue drained, platform actions done, LLM background tasks finished."""
        await self.pipe.idle(timeout_s)
        clf_tasks = getattr(self.pipe.classifier, "_tasks", set())

        async def wait() -> None:
            while clf_tasks:  # noqa: ASYNC110 - polling plain state
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), timeout_s)
        await self.pipe.idle(timeout_s)


def make_rig(
    config: ChatConfig | None = None,
    *,
    llm: LlmTransport | None = None,
    start: bool = True,
    **pipe_kwargs: Any,
) -> Rig:
    bus = InProcessBus()
    clock = pipe_kwargs.pop("clock", None) or ManualClock()
    twitch, youtube = Stub(), Stub()
    pipe = ChatPipeline(
        bus, config or ChatConfig(), llm=llm, youtube=youtube, twitch=twitch, clock=clock, **pipe_kwargs
    )
    if start:
        pipe.start()
    return Rig(bus, pipe, twitch, youtube, clock)
