"""Token storage that works in headless WSL: the OS keyring when a real one exists, else a 0600 file.

``default_backend()`` returns an object with the keyring ``get_password`` / ``set_password`` API that the
YouTube and Twitch token stores accept. Order: an allowed OS keyring (Secret Service, macOS, Windows) first;
when there is none, or it fails on write, a JSON file ``chat-tokens.json`` under the data directory
(``STREAMSARTHI_TOKEN_DIR``, else ``$XDG_CONFIG_HOME/streamsarthi``, else ``~/.config/streamsarthi``). That
directory is outside the repository, so it can never be committed. The file is created with mode 0600 in a
0700 directory and replaced atomically.

Only the fixed log code ``token stored in file`` is ever logged or printed (plus a one-time notice on stderr
the first time a token is written there). A token value is never printed or logged.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from chat.common.logsafe import log_event

_LOG = logging.getLogger("chat.tokenfile")
FILE_NAME = "chat-tokens.json"
_ALLOWED = {
    "keyring.backends.SecretService.Keyring",
    "keyring.backends.macOS.Keyring",
    "keyring.backends.Windows.WinVaultKeyring",
}
_notice_shown = False


def token_dir() -> Path:
    explicit = os.environ.get("STREAMSARTHI_TOKEN_DIR", "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".config") / "streamsarthi"


def _notice(path: Path) -> None:
    global _notice_shown
    log_event(_LOG, "token_stored_in_file", level=logging.WARNING)
    if _notice_shown:
        return
    _notice_shown = True
    print(  # noqa: T201
        f"NOTICE: no usable OS keyring here, so login tokens are stored in a file: {path} (permissions 0600, "
        "outside the repo). Anyone who can read your home directory can use them. Delete the file to log out.",
        file=sys.stderr,
    )


class FileBackend:
    """Keyring-compatible store in one 0600 JSON file."""

    def __init__(self, directory: Path | None = None) -> None:
        self.path = (directory or token_dir()) / FILE_NAME

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("token_file_corrupt")
        return raw

    def get_password(self, service: str, user: str) -> str | None:
        value = self._read().get(service, {}).get(user)
        return value if isinstance(value, str) else None

    def set_password(self, service: str, user: str, value: str) -> None:
        data = self._read()
        data.setdefault(service, {})[user] = value
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            directory.chmod(0o700)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tok-")  # mkstemp creates 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        _notice(self.path)


class LayeredBackend:
    """Reads the keyring then the file; writes the keyring, falling back to the file when it is missing or fails."""

    def __init__(self, primary: Any | None, file: FileBackend) -> None:
        self.primary, self.file = primary, file

    def get_password(self, service: str, user: str) -> str | None:
        if self.primary is not None:
            with contextlib.suppress(Exception):
                value = self.primary.get_password(service, user)
                if value is not None:
                    return value
        return self.file.get_password(service, user)

    def set_password(self, service: str, user: str, value: str) -> None:
        if self.primary is not None:
            try:
                self.primary.set_password(service, user, value)
                return
            except Exception:
                log_event(_LOG, "keyring_write_failed", level=logging.INFO)
        self.file.set_password(service, user, value)


def default_backend(*, directory: Path | None = None) -> LayeredBackend:
    primary: Any | None = None
    try:
        import keyring

        candidate = keyring.get_keyring()
        if type(candidate).__module__ + "." + type(candidate).__name__ in _ALLOWED:
            primary = candidate
    except Exception:  # no keyring package, or it raised while choosing a backend
        primary = None
    return LayeredBackend(primary, FileBackend(directory))
