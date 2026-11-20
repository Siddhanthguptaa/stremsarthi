from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Outcome of a platform action. ``status`` maps onto ``ChatVerdict.platform_result``
    (``done`` / ``unsupported`` / ``failed``); ``reason`` is a machine code."""

    status: Literal["done", "unsupported", "failed"]
    reason: str
