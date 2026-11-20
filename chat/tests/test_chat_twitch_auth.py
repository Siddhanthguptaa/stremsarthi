"""Twitch device-code auth, hourly validation, single-use refresh rotation and keyring storage."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from chat.common import ChatError, HttpResponse, ManualClock, Secret
from chat.testing import FakeTwitch
from chat.twitch import (
    SCOPES,
    DeviceAuth,
    DevicePrompt,
    InvalidToken,
    Tokens,
    TokenStore,
    TwitchError,
    UserSession,
)
from chat.twitch import make_http as make_twitch_http
from chat.twitch.auth import approved_scopes


def test_scopes_are_the_new_set_not_irl_shields() -> None:
    assert set(SCOPES) == {
        "user:read:chat",
        "moderator:manage:chat_messages",
        "moderator:manage:banned_users",
    }
    assert not any(s in SCOPES for s in ("moderator:read:shield_mode", "channel:read:stream_key"))
    assert approved_scopes(list(reversed(SCOPES))) and not approved_scopes([*SCOPES, "chat:edit"])


async def test_device_flow_end_to_end(tw: SimpleNamespace) -> None:
    exchange = next(r for r in tw.fake.token_requests if r["grant_type"].endswith("device_code"))
    assert exchange["scopes"] == " ".join(SCOPES) and exchange["client_id"] == tw.fake.client_id
    assert "client_secret" not in exchange  # public client: no secret anywhere
    assert tw.session.user_id == tw.fake.user_id
    assert "access-" not in repr(tw.tokens) and "refresh-" not in repr(tw.tokens)


async def test_pending_then_slow_down_then_approved(aio: Any, clock: ManualClock) -> None:
    fake = FakeTwitch()
    await fake.start()
    try:
        waits: list[float] = []

        async def wait(seconds: float, cancel: asyncio.Event) -> None:
            waits.append(seconds)
            if len(waits) == 4:
                fake.approve()

        fake.pending_polls_before_slow_down = 1
        auth = DeviceAuth(
            fake.client_id,
            make_twitch_http(aio, loopback_origin=fake.origin),
            clock=clock.monotonic,
            wait=wait,
        )
        prompts: list[DevicePrompt] = []
        tokens = await auth.login(prompts.append, asyncio.Event())
        assert waits[:3] == [1, 6, 6]  # interval 1 s, +5 s after slow_down
        assert (
            prompts[0].uri.reveal().startswith("https://www.twitch.tv/activate")
            and prompts[0].code.reveal() == "ABCDEFGH"
        )
        assert str(prompts[0].code) == "<redacted>" and tokens.refresh.reveal().startswith("refresh-")
    finally:
        await fake.stop()


class _StubHttp:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)

    async def request(self, method: str, path: str, **kwargs: Any) -> HttpResponse:
        return self.responses.pop(0)


def device_ok(uri: str = "https://www.twitch.tv/activate?device-code=X", expires: int = 600) -> HttpResponse:
    return HttpResponse(
        200,
        {
            "device_code": "d1",
            "user_code": "U1",
            "verification_uri": uri,
            "expires_in": expires,
            "interval": 1,
        },
    )


async def instant(seconds: float, cancel: asyncio.Event) -> None:
    return None


@pytest.mark.parametrize(
    ("responses", "code"),
    [
        ([device_ok("https://evil.example/activate")], "device_invalid_endpoint"),
        ([device_ok("http://www.twitch.tv/activate")], "device_invalid_endpoint"),
        ([HttpResponse(500, {})], "device_unavailable"),
        ([device_ok(), HttpResponse(400, {"message": "access_denied"})], "device_denied"),
        ([device_ok(), HttpResponse(400, {"message": "expired_token"})], "device_expired"),
        ([device_ok(), HttpResponse(400, {"message": "something else <script>"})], "device_failed"),
        (
            [
                device_ok(),
                HttpResponse(
                    200,
                    {
                        "access_token": "a",
                        "refresh_token": "r",
                        "expires_in": 100,
                        "scope": ["chat:edit"],
                        "token_type": "bearer",
                    },
                ),
            ],
            "twitch_scope_mismatch",
        ),
    ],
)
async def test_device_flow_failures_have_fixed_codes(
    responses: list[HttpResponse], code: str, clock: ManualClock
) -> None:
    auth = DeviceAuth("cid", _StubHttp(*responses), clock=clock.monotonic, wait=instant)  # type: ignore[arg-type]
    shown: list[DevicePrompt] = []
    with pytest.raises(TwitchError) as err:
        await auth.login(shown.append, asyncio.Event())
    assert err.value.code == code
    if code == "device_invalid_endpoint":
        assert shown == []  # a secret-bearing URI is never shown before it is validated


async def test_device_flow_expiry_and_cancel(clock: ManualClock) -> None:
    async def tick(seconds: float, cancel: asyncio.Event) -> None:
        clock.advance(400)

    pending = HttpResponse(400, {"message": "authorization_pending"})
    auth = DeviceAuth(
        "cid", _StubHttp(device_ok(expires=600), pending, pending, pending), clock=clock.monotonic, wait=tick
    )  # type: ignore[arg-type]
    with pytest.raises(TwitchError) as err:
        await auth.login(lambda p: None, asyncio.Event())
    assert err.value.code == "device_expired"
    cancelled = asyncio.Event()
    cancelled.set()
    with pytest.raises(asyncio.CancelledError):
        await DeviceAuth("cid", _StubHttp(), clock=clock.monotonic).login(lambda p: None, cancelled)  # type: ignore[arg-type]


async def test_session_validates_hourly_and_refreshes_with_single_use_rotation(tw: SimpleNamespace) -> None:
    first = (await tw.session.access_token()).reveal()
    assert (await tw.session.access_token()).reveal() == first  # cached: no second validation
    old_refresh = tw.session.tokens.refresh.reveal()
    tw.clock.advance(14000)  # past expires_in minus the 60 s margin
    second = (await tw.session.access_token()).reveal()
    assert second != first and second in tw.fake.valid_tokens
    assert (
        old_refresh not in tw.fake.refresh_tokens
    )  # the old refresh token is dead; the new one is held in RAM
    assert tw.session.tokens.refresh.reveal() in tw.fake.refresh_tokens


async def test_concurrent_callers_share_one_refresh(tw: SimpleNamespace) -> None:
    tw.clock.advance(14000)
    tokens = await asyncio.gather(*(tw.session.access_token() for _ in range(6)))
    assert len({t.reveal() for t in tokens}) == 1
    assert len([r for r in tw.fake.token_requests if r["grant_type"] == "refresh_token"]) == 1


async def test_revalidates_after_an_hour_and_detects_revocation(tw: SimpleNamespace) -> None:
    tw.clock.advance(3601)
    tw.fake.valid_tokens.clear()  # the user revoked the app on twitch.tv
    with pytest.raises(InvalidToken) as err:
        await tw.session.access_token()
    assert err.value.code == "twitch_auth_failed"


async def test_pinned_user_mismatch_is_rejected(tw: SimpleNamespace) -> None:
    session = UserSession(tw.auth, tw.tokens, expected_user_id="9999")
    with pytest.raises(TwitchError) as err:
        await session.access_token()
    assert err.value.code == "twitch_validation_failed"


async def test_validation_requires_exact_scopes(tw: SimpleNamespace) -> None:
    tw.fake.scopes = [*SCOPES, "chat:edit"]
    session = UserSession(tw.auth, tw.tokens)
    with pytest.raises(TwitchError):
        await session.access_token()


async def test_failed_refresh_is_a_fixed_code(tw: SimpleNamespace) -> None:
    tw.fake.refresh_tokens.clear()
    tw.clock.advance(14000)
    with pytest.raises(TwitchError) as err:
        await tw.session.access_token()
    assert err.value.code == "twitch_refresh_failed"


class _Ring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, user: str, value: str) -> None:
        self.data[(service, user)] = value

    def get_password(self, service: str, user: str) -> str | None:
        return self.data.get((service, user))


async def test_token_store_is_opt_in_and_scope_checked(tw: SimpleNamespace) -> None:
    ring = _Ring()
    store = TokenStore(ring)
    store.save("cid", "1001", tw.tokens)
    assert ring.data == {}
    store.save("cid", "1001", tw.tokens, remember=True)
    loaded = store.load("cid", "1001")
    assert loaded is not None and loaded.refresh == tw.tokens.refresh and loaded.expires_at == 0.0
    assert store.load("cid", "other") is None
    ring.data[("stream-sarthi-twitch:cid", "1001")] = '{"access":"a","refresh":"r","scopes":["chat:edit"]}'
    with pytest.raises(ChatError) as err:
        store.load("cid", "1001")
    assert err.value.code == "keyring_read_failed"


def test_tokens_cannot_be_pickled() -> None:
    import pickle

    with pytest.raises(TypeError):
        pickle.dumps(Tokens(Secret("a"), Secret("b"), 1.0))
