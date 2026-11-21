"""eval/mock_platforms: quota accounting, scope enforcement, paging, OAuth and EventSub behaviour (spec 9 tests 7, 8)."""

import json

import aiohttp
import pytest

from eval.mock_platforms import SCOPES, TwitchMock, YouTubeMock

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def yt():
    m = YouTubeMock()
    await m.start()
    token = m.issue_access_token()
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}) as s:
        yield m, s
    await m.stop()


@pytest.fixture
async def tw():
    m = TwitchMock(keepalive_s=1)
    await m.start()
    m.approve()
    async with aiohttp.ClientSession() as s:
        yield m, s
    await m.stop()


async def test_youtube_quota_accounting_10_lists_2_deletes_1_ban(yt):
    m, s = yt
    ids = [m.add_message(f"hello {i}") for i in range(3)]
    for _ in range(10):
        r = await s.get(m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id})
        assert r.status == 200
    for mid in ids[:2]:
        r = await s.delete(m.origin + "/youtube/v3/liveChat/messages", params={"id": mid})
        assert r.status == 204
    body = {
        "snippet": {
            "liveChatId": m.live_chat_id,
            "type": "temporary",
            "banDurationSeconds": 600,
            "bannedUserDetails": {"channelId": "UC_X"},
        }
    }
    r = await s.post(m.origin + "/youtube/v3/liveChat/bans", params={"part": "snippet"}, json=body)
    assert r.status == 200
    snap = await (await s.get(m.origin + "/_quota")).json()
    assert snap["units_by_kind"] == {"list": 50, "delete": 40, "ban": 200} and snap["units_used"] == 290


async def test_youtube_paging_tokens_and_polling_interval(yt):
    m, s = yt
    for i in range(5):
        m.add_message(f"m{i}")
    r = await (
        await s.get(
            m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id, "maxResults": 3}
        )
    ).json()
    assert len(r["items"]) == 3 and r["pollingIntervalMillis"] == 5000
    r2 = await (
        await s.get(
            m.origin + "/youtube/v3/liveChat/messages",
            params={"liveChatId": m.live_chat_id, "pageToken": r["nextPageToken"]},
        )
    ).json()
    assert len(r2["items"]) == 2
    bad = await s.get(
        m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id, "pageToken": "zzz"}
    )
    assert bad.status == 400


async def test_youtube_auth_quota_exceeded_injection_and_daily_reset(yt):
    m, s = yt
    m.daily_quota = 10
    assert (
        await s.get(m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id})
    ).status == 200
    assert (
        await s.get(m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id})
    ).status == 200
    r = await s.get(m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id})
    assert r.status == 403 and (await r.json())["error"]["errors"][0]["reason"] == "quotaExceeded"
    m.expire_access_token()
    assert (
        await s.get(m.origin + "/youtube/v3/liveBroadcasts", params={"broadcastStatus": "active"})
    ).status == 401
    m.inject("list", 503, "backendError", times=1)
    tok = m.issue_access_token()
    h = {"Authorization": f"Bearer {tok}"}
    m.daily_quota = 10_000
    assert (
        await s.get(
            m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id}, headers=h
        )
    ).status == 503
    assert (
        await s.get(
            m.origin + "/youtube/v3/liveChat/messages", params={"liveChatId": m.live_chat_id}, headers=h
        )
    ).status == 200


async def test_youtube_delete_unknown_and_permissions(yt):
    m, s = yt
    assert (await s.delete(m.origin + "/youtube/v3/liveChat/messages", params={"id": "nope"})).status == 404
    mid = m.add_message("x")
    m.can_moderate = False
    assert (await s.delete(m.origin + "/youtube/v3/liveChat/messages", params={"id": mid})).status == 403


async def test_youtube_token_refresh_endpoint(yt):
    m, s = yt
    r = await s.post(
        m.origin + "/token",
        data={"client_id": m.client_id, "grant_type": "refresh_token", "refresh_token": m.refresh_token},
    )
    body = await r.json()
    assert r.status == 200 and body["token_type"] == "Bearer" and "youtube.force-ssl" in body["scope"]
    bad = await s.post(
        m.origin + "/token",
        data={"client_id": m.client_id, "grant_type": "refresh_token", "refresh_token": "x"},
    )
    assert bad.status == 400


async def _helix_token(m, s, scopes=SCOPES):
    access, _ = m.mint_tokens(tuple(scopes))
    return {"Authorization": f"Bearer {access}", "Client-Id": m.client_id}


async def test_twitch_delete_without_scope_is_refused(tw):
    m, s = tw
    mid = "m1"
    m.messages[mid] = {"chatter": "2002", "badges": (), "text_len": 3}
    h = await _helix_token(m, s, ("user:read:chat",))
    q = {"broadcaster_id": m.broadcaster_id, "moderator_id": m.user_id, "message_id": mid}
    r = await s.delete(m.origin + "/helix/moderation/chat", params=q, headers=h)
    assert r.status in (401, 403) and not m.deleted
    h2 = await _helix_token(m, s)
    assert (await s.delete(m.origin + "/helix/moderation/chat", params=q, headers=h2)).status == 204
    assert m.deleted == [mid]
    assert (await s.delete(m.origin + "/helix/moderation/chat", params=q, headers=h2)).status == 404


async def test_twitch_moderator_id_and_ban_rules(tw):
    m, s = tw
    h = await _helix_token(m, s)
    base = {"broadcaster_id": m.broadcaster_id, "moderator_id": m.user_id}
    bad_mod = await s.post(
        m.origin + "/helix/moderation/bans",
        params={**base, "moderator_id": "999"},
        json={"data": {"user_id": "5", "duration": 60}},
        headers=h,
    )
    assert bad_mod.status == 403
    assert (
        await s.post(
            m.origin + "/helix/moderation/bans",
            params=base,
            json={"data": {"user_id": "5", "duration": 0}},
            headers=h,
        )
    ).status == 400
    assert (
        await s.post(
            m.origin + "/helix/moderation/bans",
            params=base,
            json={"data": {"user_id": m.broadcaster_id, "duration": 60}},
            headers=h,
        )
    ).status == 400
    ok = await s.post(
        m.origin + "/helix/moderation/bans",
        params=base,
        json={"data": {"user_id": "5", "duration": 60}},
        headers=h,
    )
    assert ok.status == 200 and m.bans == [{"user_id": "5", "duration": 60}]
    m.lose_scope("moderator:manage:banned_users")
    assert (
        await s.post(
            m.origin + "/helix/moderation/bans",
            params=base,
            json={"data": {"user_id": "6", "duration": 60}},
            headers=h,
        )
    ).status == 401


async def test_twitch_device_flow_and_single_use_refresh(tw):
    m, s = tw
    m._approved = False
    form = {"client_id": m.client_id, "scopes": " ".join(SCOPES)}
    d = await (await s.post(m.origin + "/oauth2/device", data=form)).json()
    assert d["verification_uri"] == "https://www.twitch.tv/activate"
    grant = {
        "client_id": m.client_id,
        "device_code": d["device_code"],
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    pending = await s.post(m.origin + "/oauth2/token", data=grant)
    assert pending.status == 400 and (await pending.json())["message"] == "authorization_pending"
    m.approve()
    tok = await (await s.post(m.origin + "/oauth2/token", data=grant)).json()
    assert set(tok["scope"]) == set(SCOPES)
    ref = {"client_id": m.client_id, "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}
    assert (await s.post(m.origin + "/oauth2/token", data=ref)).status == 200
    assert (await s.post(m.origin + "/oauth2/token", data=ref)).status == 400  # single use
    v = await s.get(m.origin + "/oauth2/validate", headers={"Authorization": "OAuth " + tok["access_token"]})
    assert v.status == 200 and (await v.json())["user_id"] == m.user_id


async def test_twitch_eventsub_welcome_subscribe_notify_reconnect_drop(tw):
    m, s = tw
    h = await _helix_token(m, s)
    ws = await s.ws_connect(m.ws_url)
    welcome = json.loads((await ws.receive()).data)
    assert welcome["metadata"]["message_type"] == "session_welcome"
    sid = welcome["payload"]["session"]["id"]
    cond = {"broadcaster_user_id": m.broadcaster_id, "user_id": m.user_id}
    sub = await s.post(
        m.origin + "/helix/eventsub/subscriptions",
        headers=h,
        json={
            "type": "channel.chat.message",
            "version": "1",
            "condition": cond,
            "transport": {"method": "websocket", "session_id": sid},
        },
    )
    rec = (await sub.json())["data"][0]
    assert sub.status == 202 and rec["status"] == "enabled" and rec["condition"] == cond
    await m.send_chat("hello", "2002", "viewer", badges=("moderator",), message_id="mm1")
    note = json.loads((await ws.receive()).data)
    ev = note["payload"]["event"]
    assert note["metadata"]["message_type"] == "notification" and ev["message"]["text"] == "hello"
    assert ev["badges"][0]["set_id"] == "moderator"
    await m.send_reconnect()
    rc = json.loads((await ws.receive()).data)
    assert rc["metadata"]["message_type"] == "session_reconnect"
    ws2 = await s.ws_connect(rc["payload"]["session"]["reconnect_url"])
    assert json.loads((await ws2.receive()).data)["metadata"]["message_type"] == "session_welcome"
    await m.send_chat("after", "2002", "viewer", message_id="mm2")
    assert json.loads((await ws2.receive()).data)["payload"]["event"]["message"]["text"] == "after"
    assert m.drop() >= 1
    msg = await ws2.receive()
    assert msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR)


async def test_twitch_subscribe_rejects_unknown_session_and_missing_scope(tw):
    m, s = tw
    h = await _helix_token(m, s)
    cond = {"broadcaster_user_id": m.broadcaster_id, "user_id": m.user_id}
    body = {
        "type": "channel.chat.message",
        "version": "1",
        "condition": cond,
        "transport": {"method": "websocket", "session_id": "nope"},
    }
    assert (await s.post(m.origin + "/helix/eventsub/subscriptions", headers=h, json=body)).status == 400
    h2 = await _helix_token(m, s, ("moderator:manage:chat_messages",))
    assert (await s.post(m.origin + "/helix/eventsub/subscriptions", headers=h2, json=body)).status == 401
