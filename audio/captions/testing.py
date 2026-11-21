"""Test doubles for captions: a scripted translator that records exactly what was "sent to the API"."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ClassVar

from audio.captions.translate import TranslateProviderError


class FakeTranslator:
    """Stands in for Sarvam. ``sent`` is what would have left the machine (tests assert no PII is in it)."""

    name: ClassVar[str] = "sarvam"
    paid: ClassVar[bool] = True
    available = True

    def __init__(
        self,
        latency: float = 0.2,
        fail: BaseException | Callable[[], BaseException | None] | None = None,
        mangle_mask: bool = False,
    ) -> None:
        self.latency = latency
        self.fail = fail
        self.mangle_mask = mangle_mask
        self.sent: list[str] = []
        self.calls = 0

    async def _op(self, tag: str, text: str, tgt: str) -> str:
        self.calls += 1
        self.sent.append(text)
        if self.latency:
            await asyncio.sleep(self.latency)
        err = self.fail() if callable(self.fail) else self.fail
        if err is not None:
            raise err
        out = f"{tgt}:{text}"
        return out.replace("▮", "#") if self.mangle_mask else out

    async def translate(self, text: str, src: str, tgt: str) -> str:
        return await self._op("t", text, tgt)

    async def transliterate(self, text: str, src: str, tgt: str) -> str:
        return await self._op("x", text, tgt)


def provider_down() -> TranslateProviderError:
    return TranslateProviderError("provider error (HTTP 503)", 503)
