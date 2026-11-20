"""Privacy-safe logging helpers.

Rule (docs/specs/chat.md 5.6): raw message text, author names, streamer private terms, phone
numbers, tokens and response bodies are never logged. Log lines carry an event code, a hashed
message id and numbers only. ``log_event`` enforces that mechanically: any string field that is
not a short machine code is replaced by ``<redacted>``.
"""

from __future__ import annotations

import hashlib
import logging
import re

_CODE_RE = re.compile(r"^[A-Za-z0-9_:.\-/]{1,64}$")


def hash_id(value: str) -> str:
    """Stable short digest of an id (message id, author id) for correlating log lines."""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def _render(value: object) -> str:
    if value is None or isinstance(value, bool | int | float):
        return str(value)
    if isinstance(value, str) and _CODE_RE.match(value):
        return value
    return "<redacted>"


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: object) -> None:
    """Log ``event k=v ...``. ``event`` must be a machine code; string values must be codes too."""
    if not _CODE_RE.match(event):
        event = "bad_event_code"
    if not logger.isEnabledFor(level):
        return
    parts = [event]
    parts.extend(f"{key}={_render(value)}" for key, value in fields.items())
    logger.log(level, " ".join(parts))
