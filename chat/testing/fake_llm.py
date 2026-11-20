"""Scriptable fake LLM transport for the classifier tests (no network, no API key)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from chat.classify.llm import LlmError, LlmRateLimited

Step = str | Exception | Callable[[list[str]], str]


def reply(results: list[dict[str, Any]]) -> str:
    return json.dumps({"results": results})


def ok(
    i: int, labels: list[str] | None = None, confidence: float = 0.95, target: str = "none"
) -> dict[str, Any]:
    return {
        "i": i,
        "labels": labels if labels is not None else ["clean"],
        "confidence": confidence,
        "target": target,
    }


class FakeLlm:
    """Plays ``script`` one step per call. A step is a raw reply string, an exception to raise, or a callable
    receiving the message texts of the call. After the script ends, ``default`` answers (all clean)."""

    def __init__(
        self,
        script: list[Step] | None = None,
        *,
        default: Callable[[list[str]], str] | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.script = list(script or [])
        self.default = default or (lambda texts: reply([ok(i) for i in range(len(texts))]))
        self.delay_s = delay_s
        self.calls: list[dict[str, Any]] = []

    @property
    def texts_seen(self) -> list[list[str]]:
        return [[m["t"] for m in json.loads(c["user"])["messages"]] for c in self.calls]

    async def complete(self, *, system: str, user: str, model: str, timeout_s: float, max_tokens: int) -> str:
        self.calls.append({"system": system, "user": user, "model": model, "timeout_s": timeout_s})
        texts = [m["t"] for m in json.loads(user)["messages"]]
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        step: Step = self.script.pop(0) if self.script else self.default
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(texts)
        return step


__all__ = ["FakeLlm", "LlmError", "LlmRateLimited", "ok", "reply"]
