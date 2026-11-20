"""Shared fixtures: virtual clock, aiohttp session, fully wired YouTube and Twitch stacks on the fakes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace

import aiohttp
import pytest

from chat.common import ManualClock, Secret
from chat.testing import FakeTwitch, FakeYouTube
from chat.testing.fake_youtube import simulate_browser
from chat.twitch import ChatEvents, DeviceAuth, UserSession
from chat.twitch import make_http as make_twitch_http
from chat.youtube import QuotaGovernor, YouTubeAuth, YouTubeClient, YouTubeSession, make_http


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def aio() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
async def yt(clock: ManualClock, aio: aiohttp.ClientSession) -> AsyncIterator[SimpleNamespace]:
    fake = FakeYouTube(now=clock.wall)
    await fake.start()
    http = make_http(aio, loopback_origin=fake.origin)
    auth = YouTubeAuth(fake.client_id, Secret("specimen-secret"), http, clock=clock, auth_url=fake.auth_url)
    browsers: list[asyncio.Future[None]] = []
    tokens = await auth.login(lambda url: browsers.append(asyncio.ensure_future(simulate_browser(aio, url))))
    await asyncio.gather(*browsers)
    statuses: list[str] = []
    governor = QuotaGovernor(clock=clock, on_event=statuses.append)
    session = YouTubeSession(auth, tokens, clock=clock)
    client = YouTubeClient(http, session, governor)
    yield SimpleNamespace(
        fake=fake,
        http=http,
        auth=auth,
        tokens=tokens,
        session=session,
        governor=governor,
        client=client,
        clock=clock,
        statuses=statuses,
    )
    await fake.stop()


@pytest.fixture
async def tw(clock: ManualClock, aio: aiohttp.ClientSession) -> AsyncIterator[SimpleNamespace]:
    fake = FakeTwitch()
    await fake.start()
    http = make_twitch_http(aio, loopback_origin=fake.origin)
    auth = DeviceAuth(fake.client_id, http, clock=clock.monotonic, wait=_instant_wait(clock))
    fake.approve()
    tokens = await auth.login(lambda prompt: None, asyncio.Event())
    session = UserSession(auth, tokens)
    await session.access_token()  # validates and learns the user id
    yield SimpleNamespace(
        fake=fake, http=http, auth=auth, tokens=tokens, session=session, clock=clock, aio=aio
    )
    await fake.stop()


def _instant_wait(clock: ManualClock):  # type: ignore[no-untyped-def]
    async def wait(seconds: float, cancel: asyncio.Event) -> None:
        if cancel.is_set():
            raise asyncio.CancelledError
        await clock.sleep(seconds)

    return wait


@pytest.fixture
def twitch_events_factory():  # type: ignore[no-untyped-def]
    def make(broadcaster_id: str = "1001", **kwargs):  # type: ignore[no-untyped-def]
        received: list = []
        statuses: list[str] = []
        events = ChatEvents(broadcaster_id, received.append, statuses.append, **kwargs)
        return events, received, statuses

    return make
