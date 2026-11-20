"""Explicit opt-in OS credential storage; never a plaintext fallback.

Adapted from reference/irl-shield/shield/twitch/storage.py (MIT, Copyright (c) 2026 Elan Nash; licence in
``LICENSE.irl-shield``). Changes: new scope set, keyring service name, ``TwitchError`` codes, and the
``/dev/tty`` owner prompt is removed (``DeviceAuth.login`` takes a ``present`` callback instead).
"""

from __future__ import annotations

import json
from typing import Any

from chat.common.secret import Secret

from .auth import SCOPES, Tokens, approved_scopes
from .errors import TwitchError

_ALLOWED_BACKENDS = {
    "keyring.backends.SecretService.Keyring",
    "keyring.backends.macOS.Keyring",
    "keyring.backends.Windows.WinVaultKeyring",
}


class TokenStore:
    def __init__(self, backend: Any = None) -> None:
        if backend is None:
            import keyring

            backend = keyring.get_keyring()
            if type(backend).__module__ + "." + type(backend).__name__ not in _ALLOWED_BACKENDS:
                raise TwitchError("keyring_unavailable")
        self.backend = backend

    def save(self, client_id: str, user_id: str, tokens: Tokens, *, remember: bool = False) -> None:
        if remember is not True:
            return
        try:
            self.backend.set_password(
                "stream-sarthi-twitch:" + client_id,
                user_id,
                json.dumps(
                    {
                        "access": tokens.access.reveal(),
                        "refresh": tokens.refresh.reveal(),
                        "scopes": list(SCOPES),
                    }
                ),
            )
        except Exception:
            raise TwitchError("keyring_write_failed") from None

    def load(self, client_id: str, user_id: str) -> Tokens | None:
        try:
            raw = self.backend.get_password("stream-sarthi-twitch:" + client_id, user_id)
            if raw is None:
                return None
            data = json.loads(raw)
            if not isinstance(data, dict) or not approved_scopes(data.get("scopes")):
                raise ValueError
            # Monotonic deadlines cannot survive a process; refresh on load.
            return Tokens(Secret(data["access"]), Secret(data["refresh"]), 0.0)
        except Exception:
            raise TwitchError("keyring_read_failed") from None
