"""Stream keys held in RAM only (never written, never logged, never returned).

A key can come from ``.env`` (the engine reads it itself) or be typed into the setup screen by the
streamer. The second kind lives here for the life of the API process and is handed to the engine
child through its environment at spawn time. Nothing in this module returns a key to a caller other
than ``child_env`` (used by the orchestrator when it builds the engine's environment).
"""

from __future__ import annotations

import re
from typing import Literal

DestinationKind = Literal["youtube", "twitch"]
ENV_NAME: dict[str, str] = {"youtube": "YOUTUBE_STREAM_KEY", "twitch": "TWITCH_STREAM_KEY"}
# Printable, no whitespace, no quotes. YouTube keys look like xxxx-xxxx-xxxx-xxxx-xxxx, Twitch like live_<id>_<token>.
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.~]{8,200}$")


class InvalidKey(ValueError):
    """The key does not look like a stream key (the message never contains the key)."""


class DestinationKeys:
    def __init__(self) -> None:
        self._keys: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"DestinationKeys(configured={sorted(self._keys)})"

    def set(self, kind: str, key: str) -> None:
        if kind not in ENV_NAME:
            raise InvalidKey("unknown destination")
        key = key.strip()
        if not _KEY_RE.match(key):
            raise InvalidKey("stream key has an unexpected format")
        self._keys[kind] = key

    def clear(self, kind: str) -> bool:
        return self._keys.pop(kind, None) is not None

    def clear_all(self) -> None:
        self._keys.clear()

    def has(self, kind: str) -> bool:
        return kind in self._keys

    def kinds(self) -> list[str]:
        return sorted(self._keys)

    def child_env(self) -> dict[str, str]:
        """Environment entries for the engine child. The only place a key leaves this object."""
        return {ENV_NAME[k]: v for k, v in self._keys.items()}
