"""T4.6: moderator console endpoints, role gate, the API's chat state and the streamer overlay privacy.

Message text in these tests is a visible synthetic marker (``HIDDEN-TEXT-MARKER-...``): the privacy
assertions look for it everywhere the streamer role, the logs or a resolved item must not carry it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.chat_state import ChatState
from contracts import (
    Category,
    ChatAction,
    ChatMessage,
    ChatVerdict,
    ModAction,
    ModQueueItem,
    Topic,
)

H = {"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:8000"}
MARK = "HIDDEN-TEXT-MARKER"


def msg(mid: str, text: str | None = None, *, staff: bool = False, platform: str = "twitch") -> ChatMessage:
    return ChatMessage(
        platform=platform,  # type: ignore[arg-type]
        message_id=mid,
        channel_id="chan1",
        author_id=f"author-{mid}",
        author_display="SPECIMEN viewer",
        text=text if text is not None else f"{MARK}-{mid}",
        received_at=1_700_000_000.0,
        is_broadcaster=False,
        is_moderator=staff,
    )


def verdict(
    mid: str,
    action: ChatAction = ChatAction.HOLD_FOR_MOD,
    *,
    reason: str = "rules_abuse",
    result: str = "pending",
    platform: str = "twitch",
    timeout: int | None = None,
) -> ChatVerdict:
    cats = () if action is ChatAction.ALLOW else (Category.CHAT_ABUSE,)
    return ChatVerdict(
        platform=platform,  # type: ignore[arg-type]
        message_id=mid,
        action=action,
        categories=cats,
        confidence=0.93,
        reason=reason,
        stage="rules",
        latency_ms=1.0,
        timeout_seconds=timeout,
        platform_result=result,  # type: ignore[arg-type]
    )


def item(
    mid: str, state: str = "pending", *, staff: bool = False, platform: str = "twitch", **kw: Any
) -> ModQueueItem:
    m, v = msg(mid, staff=staff, platform=platform), verdict(mid, platform=platform, **kw)
    return ModQueueItem(m, v, 1_700_000_001.0, state)  # type: ignore[arg-type]


@pytest.fixture
def mod_headers(app) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.state.tokens.issue('moderator')}"}


@pytest.fixture
def feed(publish, app, wait_until) -> Callable[..., None]:
    """Publish chat events and wait until the hub has digested them."""

    def _feed(*events: tuple[Topic, Any]) -> None:
        start = app.state.hub.seq
        for topic, event in events:
            publish(topic, event)
        assert wait_until(lambda: app.state.hub.seq >= start + len(events))

    return _feed


def acts(bus) -> list[ModAction]:
    return [e for t, e in bus.bus.history if t is Topic.CHAT_MODACTION]


def body(mid: str = "m1", action: str = "delete", **kw: Any) -> dict[str, Any]:
    return {"message_id": mid, "platform": "twitch", "action": action, **kw}


# --- role gate -----------------------------------------------------------------------------------------


def test_queue_and_action_are_moderator_only(client, auth, mod_headers, role_headers):
    assert client.get("/api/mod/queue").status_code == 401
    assert client.post("/api/mod/action", json=body()).status_code == 401
    for headers in (auth, role_headers("hotkey"), role_headers("viewer-readonly")):
        assert client.get("/api/mod/queue", headers=headers).status_code == 403
        assert client.post("/api/mod/action", json=body(), headers=headers).status_code == 403
    assert client.get("/api/mod/queue", headers=mod_headers).status_code == 200


def test_streamer_cannot_reach_modqueue_text_by_any_route(client, auth, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    for path in ("/api/mod/queue", "/api/events", "/api/alerts", "/api/chat/overlay"):
        r = client.get(path, headers=auth)
        assert MARK not in r.text, path


# --- the queue snapshot is built from bus events --------------------------------------------------------


def test_late_console_snapshot_holds_pending_items_with_text(client, mod_headers, feed):
    feed(
        (Topic.CHAT_MODQUEUE, item("m1")),
        (Topic.CHAT_MODQUEUE, item("m2", staff=True, action=ChatAction.ALLOW)),
    )
    snap = client.get("/api/mod/queue", headers=mod_headers).json()
    ids = [i["d"]["message"]["message_id"] for i in snap["items"]]
    assert ids == ["m1", "m2"]
    assert snap["items"][0]["t"] == "ModQueueItem"
    assert f"{MARK}-m1" in json.dumps(snap)  # moderators do see the text
    assert snap["pending_count"] == 2 and snap["resolved"] == []
    assert snap["as_of_seq"] >= 2


def test_resolved_item_drops_its_text_and_keeps_a_stub(client, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    feed((Topic.CHAT_MODQUEUE, item("m1", "approved")))
    snap = client.get("/api/mod/queue", headers=mod_headers).json()
    assert snap["items"] == [] and snap["pending_count"] == 0
    assert snap["resolved"] == [
        {
            "platform": "twitch",
            "message_id": "m1",
            "state": "approved",
            "categories": ["chat_abuse"],
            "platform_result": "pending",
        }
    ]
    assert MARK not in json.dumps(snap)


def test_platform_result_from_the_verdict_reaches_the_item(client, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    feed((Topic.CHAT_VERDICT, verdict("m1", ChatAction.DELETE, reason="mod_delete", result="failed")))
    snap = client.get("/api/mod/queue", headers=mod_headers).json()
    assert snap["items"][0]["d"]["verdict"]["platform_result"] == "failed"
    assert snap["items"][0]["d"]["state"] == "pending"  # a failed platform call leaves it open for a retry
    # The result survives the republish of the closed item (the chat process republishes its older verdict copy).
    feed((Topic.CHAT_VERDICT, verdict("m1", ChatAction.DELETE, reason="mod_delete", result="done")))
    feed((Topic.CHAT_MODQUEUE, item("m1", "deleted")))
    stub = client.get("/api/mod/queue", headers=mod_headers).json()["resolved"][0]
    assert stub["state"] == "deleted" and stub["platform_result"] == "done"


# --- hidden_count rule ----------------------------------------------------------------------------------


def test_hidden_count_follows_the_latest_verdict_of_each_message():
    s = ChatState()
    s.observe(Topic.CHAT_VERDICT, verdict("a", ChatAction.HIDE_FROM_STREAMER, reason="pending_llm"))
    assert s.hidden_count == 1  # a provisional hide counts
    s.observe(Topic.CHAT_VERDICT, verdict("a", ChatAction.HOLD_FOR_MOD))
    assert s.hidden_count == 1  # same message, still hidden: counted once
    s.observe(Topic.CHAT_VERDICT, verdict("b", ChatAction.DELETE))
    assert s.hidden_count == 2
    s.observe(Topic.CHAT_VERDICT, verdict("a", ChatAction.ALLOW, reason="mod_approved", result="done"))
    assert s.hidden_count == 1  # approved or downgraded: taken back out
    s.observe(Topic.CHAT_VERDICT, verdict("c", ChatAction.ALLOW, reason="clean"))
    assert s.hidden_count == 1  # an ALLOW that was never hidden changes nothing
    s.observe(Topic.CHAT_VERDICT, verdict("a", ChatAction.ALLOW, reason="clean"))
    assert s.hidden_count == 1  # and repeating it does not go negative
    s.reset_overlay()
    assert s.hidden_count == 0


def test_staff_flagged_for_a_mod_is_queued_but_never_hidden(client, mod_headers, feed):
    feed(
        (Topic.CHAT_VERDICT, verdict("s1", ChatAction.ALLOW, reason="staff_dox_flag")),
        (Topic.CHAT_MODQUEUE, item("s1", staff=True, action=ChatAction.ALLOW, reason="staff_dox_flag")),
    )
    snap = client.get("/api/mod/queue", headers=mod_headers).json()
    assert snap["hidden_count"] == 0 and snap["pending_count"] == 1


def test_ttl_and_cap_bound_the_queue():
    now = [0.0]
    s = ChatState(max_items=10, ttl_s=100, clock=lambda: now[0])
    for i in range(14):
        s.observe(Topic.CHAT_MODQUEUE, item(f"m{i}"))
    ids = [i.message.message_id for i in s.pending_items()]
    assert ids == [f"m{i}" for i in range(4, 14)] and s.evicted == 4  # oldest evicted first
    now[0] = 101.0
    assert s.pending_items() == [] and s.expired == 10  # text gone with the TTL


def test_resolved_stubs_are_capped():
    s = ChatState()
    for i in range(260):
        s.observe(Topic.CHAT_MODQUEUE, item(f"m{i}", "dismissed"))
    assert len(s.resolved_stubs()) == 200


# --- actions --------------------------------------------------------------------------------------------


def test_action_publishes_one_modaction_with_a_hashed_actor(client, app, bus, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    r = client.post("/api/mod/action", json=body("m1", "timeout", timeout_seconds=600), headers=mod_headers)
    assert r.status_code == 200 and r.json() == {"accepted": True, "action": "timeout"}
    (sent,) = acts(bus)
    assert (sent.message_id, sent.platform, sent.action, sent.timeout_seconds) == (
        "m1",
        "twitch",
        "timeout",
        600,
    )
    token = mod_headers["Authorization"].split()[1]
    assert sent.actor and token not in sent.actor and len(sent.actor) == 16


@pytest.mark.parametrize("action", ["approve", "delete", "dismiss"])
def test_each_action_is_accepted(client, bus, mod_headers, feed, action):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    assert client.post("/api/mod/action", json=body("m1", action), headers=mod_headers).status_code == 200
    assert [a.action for a in acts(bus)] == [action]


def test_bad_bodies_are_422_and_publish_nothing(client, bus, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    bad = [
        body("m1", "timeout"),  # no duration
        body("m1", "timeout", timeout_seconds=0),
        body("m1", "timeout", timeout_seconds=1_209_601),
        body("m1", "delete", timeout_seconds=60),  # duration only for a timeout
        body("m1", "ban"),
        {**body("m1"), "platform": "tiktok"},
        {**body("m1"), "extra": 1},
        body(""),
        body("x" * 129),
    ]
    for b in bad:
        r = client.post("/api/mod/action", json=b, headers=mod_headers)
        assert r.status_code == 422, b
        assert MARK not in r.text
    assert acts(bus) == []


def test_action_on_unknown_or_resolved_message(client, bus, mod_headers, feed):
    assert client.post("/api/mod/action", json=body("nope"), headers=mod_headers).status_code == 404
    feed((Topic.CHAT_MODQUEUE, item("m1", "deleted")))
    r = client.post("/api/mod/action", json=body("m1", "approve"), headers=mod_headers)
    assert r.status_code == 409 and r.json()["error"] == "not_pending"
    assert acts(bus) == []


def test_platform_is_part_of_the_message_identity(client, bus, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1", platform="youtube")))
    r = client.post(
        "/api/mod/action", json=body("m1", "delete"), headers=mod_headers
    )  # twitch: not the same message
    assert r.status_code == 404 and acts(bus) == []


def test_staff_messages_can_be_approved_or_dismissed_but_never_removed(client, bus, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("s1", staff=True, action=ChatAction.ALLOW, reason="staff_dox_flag")))
    for action, extra in (("delete", {}), ("timeout", {"timeout_seconds": 60})):
        r = client.post("/api/mod/action", json=body("s1", action, **extra), headers=mod_headers)
        assert r.status_code == 409 and r.json()["error"] == "staff_message"
    assert client.post("/api/mod/action", json=body("s1", "dismiss"), headers=mod_headers).status_code == 200
    assert [a.action for a in acts(bus)] == ["dismiss"]


def test_double_click_is_busy_until_the_chat_process_answers(client, bus, mod_headers, feed):
    feed((Topic.CHAT_MODQUEUE, item("m1")))
    assert client.post("/api/mod/action", json=body("m1", "delete"), headers=mod_headers).status_code == 200
    r = client.post("/api/mod/action", json=body("m1", "timeout", timeout_seconds=60), headers=mod_headers)
    assert r.status_code == 409 and r.json()["error"] == "busy"
    # The platform call failed: the answer clears the guard and the moderator can retry.
    feed((Topic.CHAT_VERDICT, verdict("m1", ChatAction.DELETE, reason="mod_delete", result="failed")))
    assert client.post("/api/mod/action", json=body("m1", "delete"), headers=mod_headers).status_code == 200
    assert len(acts(bus)) == 2


def test_action_rate_limit(app, bus, settings_factory, feed):
    from app.api.main import create_app

    limited = create_app(settings_factory(mod_action_limit=2, mod_action_window_s=60.0), bus)
    with TestClient(limited, base_url="http://127.0.0.1:8000") as c:
        h = {"Authorization": f"Bearer {limited.state.tokens.issue('moderator')}"}
        codes = [c.post("/api/mod/action", json=body(f"m{i}"), headers=h).status_code for i in range(3)]
        assert codes == [404, 404, 429]  # the limiter counts attempts, not outcomes


def test_publish_failure_is_503_and_not_marked_busy(client, app, mod_headers, feed, monkeypatch):
    feed((Topic.CHAT_MODQUEUE, item("m1")))

    def boom(*_a: Any) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(app.state.bus, "publish", boom)
    assert client.post("/api/mod/action", json=body("m1"), headers=mod_headers).status_code == 503
    assert app.state.hub.chat.requested("twitch", "m1") is None


# --- websocket: what each role receives ----------------------------------------------------------------


def open_ws(client: TestClient, token: str, topics: list[str]):
    ws = client.websocket_connect("/api/ws", headers=H)
    conn = ws.__enter__()
    conn.send_text(json.dumps({"type": "auth", "token": token}))
    conn.send_text(json.dumps({"type": "subscribe", "topics": topics}))
    assert conn.receive_json()["type"] == "subscribed"
    return ws, conn


def test_late_connecting_console_is_replayed_the_pending_queue(client, app, mod_headers, feed):
    feed(
        (Topic.CHAT_VERDICT, verdict("m1")),
        (Topic.CHAT_MODQUEUE, item("m1")),
        (Topic.CHAT_MODQUEUE, item("m2")),
        (Topic.CHAT_MODQUEUE, item("m3", "dismissed")),
    )
    token = mod_headers["Authorization"].split()[1]
    ws, conn = open_ws(client, token, ["chat.modqueue", "chat.verdict"])
    try:
        got = [conn.receive_json() for _ in range(4)]
        markers = [f for f in got if f.get("type") == "replay"]
        assert {m["topic"] for m in markers} == {"chat.modqueue", "chat.verdict"}
        queue_marker = next(m for m in markers if m["topic"] == "chat.modqueue")
        assert (
            queue_marker["count"] == 2
            and queue_marker["pending_count"] == 2
            and queue_marker["hidden_count"] == 1
        )
        frames = [f for f in got if f.get("topic") == "chat.modqueue" and "data" in f]
        assert [f["data"]["d"]["message"]["message_id"] for f in frames] == [
            "m1",
            "m2",
        ]  # resolved not replayed
    finally:
        ws.__exit__(None, None, None)


def test_streamer_gets_allowed_text_and_a_hidden_count_but_never_hidden_text(client, auth, feed):
    ws, conn = open_ws(
        client, auth["Authorization"].split()[1], ["chat.allowed", "chat.verdict", "safety.event"]
    )
    try:
        seen = [conn.receive_json(), conn.receive_json()]  # the two replay markers (nothing to replay yet)
        assert {m["topic"] for m in seen} == {"chat.allowed", "chat.verdict"}
        feed(
            (Topic.CHAT_VERDICT, verdict("h1")),
            (Topic.CHAT_MODQUEUE, item("h1")),  # never delivered to a streamer
            (Topic.CHAT_VERDICT, verdict("a1", ChatAction.ALLOW, reason="clean")),
            (Topic.CHAT_ALLOWED, msg("a1", "hello from a viewer")),
        )
        frames = [conn.receive_json(), conn.receive_json(), conn.receive_json()]
        blob = json.dumps(frames)
        assert MARK not in blob and "chat.modqueue" not in blob
        verdicts = [f for f in frames if f["topic"] == "chat.verdict"]
        assert [f["meta"]["hidden_count"] for f in verdicts] == [1, 1]
        assert all("text" not in f["data"]["d"] and "author_display" not in f["data"]["d"] for f in verdicts)
        allowed = next(f for f in frames if f["topic"] == "chat.allowed")
        assert allowed["data"]["d"]["text"] == "hello from a viewer"
    finally:
        ws.__exit__(None, None, None)


def test_streamer_overlay_is_replayed_to_a_late_joiner(client, auth, feed):
    feed(
        (Topic.CHAT_VERDICT, verdict("h1")),
        (Topic.CHAT_MODQUEUE, item("h1")),
        (Topic.CHAT_ALLOWED, msg("a1", "first")),
        (Topic.CHAT_ALLOWED, msg("a1", "first")),  # a duplicate id is shown once
        (Topic.CHAT_ALLOWED, msg("a2", "second")),
    )
    ws, conn = open_ws(client, auth["Authorization"].split()[1], ["chat.allowed"])
    try:
        marker = conn.receive_json()
        assert marker == {"type": "replay", "topic": "chat.allowed", "count": 2, "hidden_count": 1}
        texts = [conn.receive_json()["data"]["d"]["text"] for _ in range(2)]
        assert texts == ["first", "second"]
    finally:
        ws.__exit__(None, None, None)
    snap = client.get("/api/chat/overlay", headers=auth).json()
    assert snap["hidden_count"] == 1 and [m["text"] for m in snap["messages"]] == ["first", "second"]
    assert MARK not in json.dumps(snap)


def test_new_session_resets_the_overlay_counter(client, app, auth, feed):
    feed((Topic.CHAT_VERDICT, verdict("h1")), (Topic.CHAT_ALLOWED, msg("a1", "hi")))
    assert client.get("/api/chat/overlay", headers=auth).json()["hidden_count"] == 1
    app.state.hub.chat.reset_overlay()
    assert client.get("/api/chat/overlay", headers=auth).json() == {"hidden_count": 0, "messages": []}


# --- privacy in logs ------------------------------------------------------------------------------------


def test_message_text_never_reaches_the_logs(client, bus, mod_headers, auth, feed, caplog):
    caplog.set_level(logging.DEBUG)
    feed(
        (Topic.CHAT_VERDICT, verdict("m1")),
        (Topic.CHAT_MODQUEUE, item("m1")),
        (Topic.CHAT_VERDICT, verdict("m1", ChatAction.DELETE, reason="mod_delete", result="done")),
        (Topic.CHAT_MODQUEUE, item("m1", "deleted")),
    )
    client.get("/api/mod/queue", headers=mod_headers)
    client.post("/api/mod/action", json=body("m1", "delete"), headers=mod_headers)  # 409: already deleted
    client.post("/api/mod/action", json=body("ghost"), headers=mod_headers)
    client.post("/api/mod/action", json={**body("m1"), "action": "ban"}, headers=mod_headers)
    client.get("/api/chat/overlay", headers=auth)
    assert MARK not in caplog.text


def test_state_repr_and_items_do_not_print_text():
    s = ChatState()
    s.observe(Topic.CHAT_MODQUEUE, item("m1"))
    assert MARK not in repr(s) and MARK not in repr(s.pending_items())
    assert dataclasses.is_dataclass(s.pending_items()[0])
