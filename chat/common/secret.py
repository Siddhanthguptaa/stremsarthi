"""Credential wrapper that never prints and refuses pickling.

Adapted from reference/irl-shield/shield/twitch/auth.py (MIT, Copyright (c) 2026 Elan Nash):
``Secret`` moved to a shared module and raises ``SecretError`` instead of a Twitch error.
"""

from __future__ import annotations

from typing import NoReturn

from .errors import SecretError


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if (
            not isinstance(value, str)
            or not value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
        ):
            raise SecretError("missing_credential")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:  # constant-time is not needed: never compared to input
        return isinstance(other, Secret) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self) -> NoReturn:
        raise TypeError("credentials cannot be serialized")
