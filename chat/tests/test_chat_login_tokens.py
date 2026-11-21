"""Headless-WSL token storage (0600 file fallback), standalone login, and the 'chat unavailable' status events."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from chat.common import Secret
from chat.live import ChatService, ExitCode, LiveOptions, run_login, youtube_login_port
from chat.tokenfile import FILE_NAME, FileBackend, default_backend
from chat.twitch import Tokens, TokenStore
from chat.youtube import YouTubeTokens, YouTubeTokenStore
from contracts import Category, Config, Topic
from contracts.config import ChatConfig
from contracts.fakes import InProcessBus

SECRET = "refresh-SPECIMEN-0123456789"


@pytest.fixture
def no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())


def test_file_fallback_when_keyring_is_unusable(
    no_keyring: None, tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    backend = default_backend(directory=tmp_path)
    assert backend.primary is None
    store = YouTubeTokenStore(backend)
    store.save("cid", YouTubeTokens(Secret("access-x"), Secret(SECRET), 0.0), remember=True)
    path = tmp_path / FILE_NAME
    assert path.exists()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = YouTubeTokenStore(default_backend(directory=tmp_path)).load("cid")  # a second process would do this
    assert loaded is not None and loaded.refresh is not None and loaded.refresh.reveal() == SECRET
    err = capsys.readouterr().err
    assert "token" in err.lower() and "file" in err.lower()
    assert SECRET not in err and SECRET not in caplog.text and "access-x" not in caplog.text
    assert "token_stored_in_file" in caplog.text


def test_notice_is_shown_once(no_keyring: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import chat.tokenfile as tf

    tf._notice_shown = False
    b = FileBackend(tmp_path)
    b.set_password("svc", "a", "v1")
    b.set_password("svc", "b", "v2")
    assert capsys.readouterr().err.count("NOTICE") == 1
    assert b.get_password("svc", "b") == "v2" and b.get_password("svc", "zz") is None


def test_twitch_store_roundtrip_through_file(no_keyring: None, tmp_path: Path) -> None:
    from chat.twitch.auth import SCOPES  # noqa: F401

    store = TokenStore(default_backend(directory=tmp_path))
    store.save("cid", "1001", Tokens(Secret("a"), Secret("r"), 0.0), remember=True)
    loaded = TokenStore(default_backend(directory=tmp_path)).load("cid", "1001")
    assert loaded is not None and loaded.refresh.reveal() == "r"


def test_failing_keyring_write_falls_back_to_file(tmp_path: Path) -> None:
    class Broken:
        def get_password(self, *_: str) -> None:
            raise RuntimeError("no dbus")

        def set_password(self, *_: str) -> None:
            raise RuntimeError("no dbus")

    from chat.tokenfile import LayeredBackend

    backend = LayeredBackend(Broken(), FileBackend(tmp_path))
    backend.set_password("s", "u", "v")
    assert backend.get_password("s", "u") == "v"


def test_login_port_is_fixed_and_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAT_YT_LOGIN_PORT", raising=False)
    assert youtube_login_port() == 8765
    monkeypatch.setenv("CHAT_YT_LOGIN_PORT", "9001")
    assert youtube_login_port() == 9001
    monkeypatch.setenv("CHAT_YT_LOGIN_PORT", "80")
    assert youtube_login_port() == 8765


async def test_standalone_login_needs_no_bus_and_reports_missing_credentials() -> None:
    cfg = Config(chat=ChatConfig(enabled=True), groq_api_key=None, youtube_oauth_client_id=None, twitch_client_id=None)
    assert await asyncio.wait_for(run_login(cfg), 5) == ExitCode.NO_CONNECTORS


async def _none(_auth: object) -> None:
    return None


async def test_missing_login_publishes_system_status_events() -> None:
    bus = InProcessBus()
    sub = bus.subscribe([Topic.SAFETY_EVENT])
    cfg = Config(
        chat=ChatConfig(enabled=True, platforms=["youtube"]),
        groq_api_key=None,
        youtube_oauth_client_id="specimen-client-id",
    )
    service = ChatService(cfg, bus, LiveOptions(youtube_tokens=_none))
    service.announce_gap_s = 0.0
    assert await asyncio.wait_for(service.run(), 5) == ExitCode.NO_CONNECTORS
    events = [sub.get_nowait()[1] for _ in range(len(sub))]
    assert events and all(e.category == Category.SYSTEM_STATUS for e in events)
    codes = {e.detail_code for e in events}
    assert {"chat_unavailable", "yt_login_required"} <= codes


async def test_loopback_uses_requested_port_and_accepts_a_pasted_redirect() -> None:
    import socket

    from chat.youtube.auth import LoopbackReceiver

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    receiver = LoopbackReceiver("st4te", free)
    assert await receiver.start() == free
    try:
        assert receiver.submit_redirect(f"http://127.0.0.1:{free}/callback?code=c&state=wrong") is False
        assert receiver.submit_redirect(f"http://127.0.0.1:{free}/callback?code=abc&state=st4te") is True
        assert await receiver.wait_code(1.0) == "abc"
    finally:
        await receiver.close()
