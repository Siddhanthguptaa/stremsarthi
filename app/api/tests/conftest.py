"""Shared fixtures: settings factory, in-process bus, and a TestClient bound to 127.0.0.1."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.bus import InProcessBusClient
from app.api.main import create_app
from app.api.settings import ApiSettings
from contracts import Config, HoldCommand, Topic
from contracts.samples import samples

SECRET = "test-secret-key-0123456789"
ORIGIN = "http://127.0.0.1:3000"


@pytest.fixture(autouse=True)
def hermetic_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """``create_app`` must never read the real ``.env`` or profile files during tests."""
    monkeypatch.setattr("app.api.main.load_config", lambda *a, **k: Config(_env_file=None))


FAKE_YT_KEY = "yt-FAKE-STREAM-KEY-9f3a"
FAKE_TW_KEY = "tw-FAKE-STREAM-KEY-77bd"
FAKE_GROQ_KEY = "gsk-FAKE-GROQ-KEY-123456"


@pytest.fixture
def config_with_keys() -> Config:
    """Config with fake stream and API keys, to prove none of them ever leaves the API."""
    return Config(
        _env_file=None,
        youtube_stream_key=FAKE_YT_KEY,  # type: ignore[arg-type]
        twitch_stream_key=FAKE_TW_KEY,  # type: ignore[arg-type]
        groq_api_key=FAKE_GROQ_KEY,  # type: ignore[arg-type]
    )


@pytest.fixture
def role_headers(app) -> Callable[[str], dict[str, str]]:
    return lambda role: {"Authorization": f"Bearer {app.state.tokens.issue(role)}"}


@pytest.fixture
def holds(bus: InProcessBusClient) -> Callable[[], list[HoldCommand]]:
    return lambda: [e for _t, e in bus.bus.history if _t is Topic.HOLD]


@pytest.fixture
def secret() -> str:
    return SECRET


@pytest.fixture
def origin() -> str:
    return ORIGIN


@pytest.fixture
def settings_factory() -> Callable[..., ApiSettings]:
    def make(**overrides: object) -> ApiSettings:
        base: dict[str, object] = {"secret_key": SECRET, "bus_backend": "inprocess"}
        base.update(overrides)
        return ApiSettings(_env_file=None, **base)  # type: ignore[call-arg]

    return make


@pytest.fixture
def bus() -> InProcessBusClient:
    return InProcessBusClient()


@pytest.fixture
def app(bus: InProcessBusClient, settings_factory: Callable[..., ApiSettings]):
    return create_app(settings_factory(), bus)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c


@pytest.fixture
def streamer_token(app) -> str:
    return app.state.tokens.issue("streamer")


@pytest.fixture
def auth(streamer_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {streamer_token}"}


@pytest.fixture
def sample() -> Callable[[str], Any]:
    by_name = {type(s).__name__: s for s in samples()}
    return by_name.__getitem__


@pytest.fixture
def publish(client: TestClient, bus: InProcessBusClient) -> Callable[[Topic, Any], None]:
    """Publish from the test thread into the app's event loop (the bus is not thread-safe)."""

    def _publish(topic: Topic, event: Any) -> None:
        client.portal.call(bus.publish, topic, event)  # type: ignore[union-attr]

    return _publish


def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def wait_until() -> Callable[..., bool]:
    return _wait_until


@pytest.fixture
def streamer_token_for() -> Callable[[Any], str]:
    return lambda a: a.state.tokens.issue("streamer")
