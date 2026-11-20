"""YouTube loopback PKCE OAuth against the fake Google endpoints."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from chat.common import ManualClock, Secret
from chat.testing import FakeYouTube
from chat.testing.fake_youtube import simulate_browser
from chat.youtube import YouTubeAuth, YouTubeError, YouTubeSession, YouTubeTokenStore, make_http
from chat.youtube.auth import SCOPE, make_pkce


def test_pkce_verifier_and_s256_challenge() -> None:
    verifier, challenge = make_pkce()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and "=" not in challenge
    assert make_pkce()[0] != verifier


async def test_login_runs_loopback_flow_with_pkce(yt: SimpleNamespace) -> None:
    exchange = yt.fake.token_requests[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["redirect_uri"].startswith("http://127.0.0.1:") and exchange["redirect_uri"].endswith(
        "/callback"
    )
    assert len(exchange["code_verifier"]) >= 43  # the fake verified it against the S256 challenge
    assert "client_secret" not in exchange  # the fake strips it; the client sent it only in the POST body
    assert yt.tokens.refresh is not None
    assert "access-" not in repr(yt.tokens) and "refresh-" not in repr(yt.tokens)


async def test_consent_url_contents(aio: aiohttp.ClientSession, clock: ManualClock) -> None:
    fake = FakeYouTube()
    await fake.start()
    try:
        auth = YouTubeAuth(
            fake.client_id,
            None,
            make_http(aio, loopback_origin=fake.origin),
            clock=clock,
            auth_url=fake.auth_url,
        )
        urls: list[str] = []
        task = asyncio.ensure_future(auth.login(urls.append, timeout_s=0.3))
        await asyncio.sleep(0.05)
        query = parse_qs(urlsplit(urls[0]).query)
        assert query["code_challenge_method"] == ["S256"] and query["scope"] == [SCOPE]
        assert query["redirect_uri"][0].startswith("http://127.0.0.1:")
        assert query["access_type"] == ["offline"] and len(query["state"][0]) >= 20
        with pytest.raises(YouTubeError) as err:
            await task
        assert err.value.code == "oauth_timeout"
    finally:
        await fake.stop()


async def test_stray_and_forged_callbacks_do_not_abort_login(
    aio: aiohttp.ClientSession, clock: ManualClock
) -> None:
    fake = FakeYouTube()
    await fake.start()
    try:
        auth = YouTubeAuth(
            fake.client_id,
            None,
            make_http(aio, loopback_origin=fake.origin),
            clock=clock,
            auth_url=fake.auth_url,
        )
        urls: list[str] = []
        task = asyncio.ensure_future(auth.login(urls.append, timeout_s=5))
        await asyncio.sleep(0.05)
        redirect = parse_qs(urlsplit(urls[0]).query)["redirect_uri"][0]
        async with aio.get(redirect + "?code=evil&state=wrong") as forged:
            assert forged.status == 400  # state mismatch
        port = urlsplit(redirect).port
        async with aio.get(
            redirect + "?code=evil&state=x", headers={"Host": f"evil.example:{port}"}
        ) as rebinding:
            assert rebinding.status == 400  # DNS-rebinding guard
        assert not task.done()
        await simulate_browser(aio, urls[0])
        tokens = await task
        assert tokens.refresh is not None
    finally:
        await fake.stop()


async def test_user_denied_consent(aio: aiohttp.ClientSession, clock: ManualClock) -> None:
    fake = FakeYouTube()
    await fake.start()
    try:
        auth = YouTubeAuth(
            fake.client_id,
            None,
            make_http(aio, loopback_origin=fake.origin),
            clock=clock,
            auth_url=fake.auth_url,
        )
        urls: list[str] = []
        task = asyncio.ensure_future(auth.login(urls.append, timeout_s=5))
        await asyncio.sleep(0.05)
        query = parse_qs(urlsplit(urls[0]).query)
        async with aio.get(
            query["redirect_uri"][0], params={"error": "access_denied", "state": query["state"][0]}
        ):
            pass
        with pytest.raises(YouTubeError) as err:
            await task
        assert err.value.code == "oauth_denied"
    finally:
        await fake.stop()


async def test_cancel_stops_login(aio: aiohttp.ClientSession, clock: ManualClock) -> None:
    fake = FakeYouTube()
    await fake.start()
    try:
        auth = YouTubeAuth(
            fake.client_id,
            None,
            make_http(aio, loopback_origin=fake.origin),
            clock=clock,
            auth_url=fake.auth_url,
        )
        cancel = asyncio.Event()
        task = asyncio.ensure_future(auth.login(lambda url: None, cancel=cancel))
        await asyncio.sleep(0.05)
        cancel.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await fake.stop()


async def test_session_refreshes_before_expiry_and_rotates_nothing_else(yt: SimpleNamespace) -> None:
    first = (await yt.session.access_token()).reveal()
    assert (await yt.session.access_token()).reveal() == first  # still valid: no refresh
    yt.clock.advance(3600 - 30)  # inside the 60 s refresh margin
    second = (await yt.session.access_token()).reveal()
    assert second != first and second in yt.fake.valid_tokens
    refreshes = [r for r in yt.fake.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1
    assert (
        yt.session._tokens.refresh is not None
    )  # a refresh response without a new refresh token keeps the old one


async def test_concurrent_callers_share_one_refresh(yt: SimpleNamespace) -> None:
    yt.clock.advance(4000)
    tokens = await asyncio.gather(*(yt.session.access_token() for _ in range(8)))
    assert len({t.reveal() for t in tokens}) == 1
    assert len([r for r in yt.fake.token_requests if r["grant_type"] == "refresh_token"]) == 1


async def test_revoked_refresh_token_is_reported_as_auth_failure(yt: SimpleNamespace) -> None:
    yt.fake.refresh_tokens.clear()
    with pytest.raises(YouTubeError) as err:
        await yt.session.access_token(force_refresh=True)
    assert err.value.code == "yt_auth_failed"


async def test_wrong_scope_is_rejected(aio: aiohttp.ClientSession, clock: ManualClock) -> None:
    import chat.testing.fake_youtube as module

    original = module.SCOPE
    module.SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
    fake = FakeYouTube()
    await fake.start()
    try:
        # the fake's authorize endpoint insists on the real scope, so drive the token step directly
        http = make_http(aio, loopback_origin=fake.origin)
        auth = YouTubeAuth(fake.client_id, None, http, clock=clock)
        fake.refresh_tokens.add("r1")
        from chat.youtube.auth import YouTubeTokens

        with pytest.raises(YouTubeError) as err:
            await auth.refresh(YouTubeTokens(Secret("a"), Secret("r1"), 0.0))
        assert err.value.code == "oauth_invalid_response"
    finally:
        module.SCOPE = original
        await fake.stop()


class _MemoryKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, user: str, value: str) -> None:
        self.data[(service, user)] = value

    def get_password(self, service: str, user: str) -> str | None:
        return self.data.get((service, user))


async def test_keyring_store_is_opt_in_and_keeps_refresh_only(yt: SimpleNamespace) -> None:
    ring = _MemoryKeyring()
    store = YouTubeTokenStore(ring)
    store.save("cid", yt.tokens)  # default: nothing stored
    assert ring.data == {}
    store.save("cid", yt.tokens, remember=True)
    stored = next(iter(ring.data.values()))
    assert "refresh-" in stored and "access-" not in stored
    loaded = store.load("cid")
    assert loaded is not None and loaded.refresh == yt.tokens.refresh and loaded.expires_at == 0.0
    # a loaded token set refreshes on first use
    session = YouTubeSession(yt.auth, loaded, clock=yt.clock)
    assert (await session.access_token()).reveal() in yt.fake.valid_tokens


def test_default_keyring_refuses_insecure_backends() -> None:
    class _Plain:  # not one of the allowed OS backends
        pass

    import keyring

    original = keyring.get_keyring
    keyring.get_keyring = lambda: _Plain()  # type: ignore[assignment]
    try:
        with pytest.raises(YouTubeError) as err:
            YouTubeTokenStore()
        assert err.value.code == "keyring_unavailable"
    finally:
        keyring.get_keyring = original
