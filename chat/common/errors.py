"""Errors carry a fixed machine code, never a remote response body, URL or message text."""

from __future__ import annotations


class ChatError(Exception):
    """Base class. ``code`` is a short fixed identifier (``[a-z0-9_]+``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SecretError(ChatError):
    """A credential was missing or malformed."""
