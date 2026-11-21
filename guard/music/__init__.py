"""Music presence detection and fingerprint matching (T5.1).

Engine entry point: ``guard.music:build`` (``runtime.extras.guard_music``).
"""

from __future__ import annotations

from typing import Any


def build(config: Any, bus: Any) -> Any:
    """``module:build(config, bus)`` for the engine; imports lazily so the package stays light."""
    from .engine_service import build as _build

    return _build(config, bus)


__all__ = ["build"]
