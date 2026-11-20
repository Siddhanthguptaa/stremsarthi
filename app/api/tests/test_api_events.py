"""SafetyEvent ring: upsert by event_id, filters, pagination, summary, roles, privacy."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.api.events import SafetyEventStore
from contracts import Category, Modality, SafetyEvent, Topic

BASE = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)


def ev(sample, i: int, **over) -> SafetyEvent:
    base = sample("SafetyEvent")
    fields = {
        "event_id": f"e{i}",
        "wall_ts": (BASE + timedelta(seconds=i)).isoformat(),
        "duration_ms": None,
        **over,
    }
    return dataclasses.replace(base, **fields)


def epoch(i: int) -> float:
    return (BASE + timedelta(seconds=i)).timestamp()


def test_upsert_replaces_by_event_id_counts_once_and_gets_a_new_cursor(sample):
    store = SafetyEventStore()
    assert store.upsert(ev(sample, 1)) is True
    assert store.upsert(ev(sample, 2)) is True
    first, cursor = store.query()
    assert [e.event_id for e in first] == ["e1", "e2"] and cursor == 2
    assert store.upsert(ev(sample, 1, duration_ms=1500)) is False  # the engine republishes at track end
    assert len(store) == 2
    updated, cursor2 = store.query(cursor=cursor)
    assert [(e.event_id, e.duration_ms) for e in updated] == [("e1", 1500)] and cursor2 == 3
    assert store.summary()["total"] == 2


def test_ring_keeps_only_the_latest_5000(sample):
    store = SafetyEventStore()
    for i in range(5100):
        store.upsert(ev(sample, i))
    assert len(store) == 5000
    events, _ = store.query(limit=1000)
    assert events[0].event_id == "e100"
    newest, _ = store.query(newest_first=True, limit=1)
    assert newest[0].event_id == "e5099"


def test_filters_by_modality_category_time_and_session(sample):
    store = SafetyEventStore()
    store.upsert(ev(sample, 1, modality=Modality.VIDEO, category=Category.FACE))
    store.upsert(ev(sample, 2, modality=Modality.AUDIO, category=Category.SPOKEN_PHONE, bbox=None))
    store.upsert(
        ev(sample, 3, modality=Modality.CHAT, category=Category.CHAT_ABUSE, bbox=None, session_id="s2")
    )
    ids = lambda **kw: [e.event_id for e in store.query(**kw)[0]]  # noqa: E731
    assert ids(modalities=["audio"]) == ["e2"]
    assert ids(modalities=["audio", "chat"]) == ["e2", "e3"]
    assert ids(categories=["face"]) == ["e1"]
    assert ids(since=epoch(2)) == ["e2", "e3"]
    assert ids(until=epoch(2)) == ["e1", "e2"]
    assert ids(since=epoch(2), until=epoch(2)) == ["e2"]
    assert ids(session_id="s2") == ["e3"]
    assert ids(modalities=["video"], categories=["pan"]) == []


def test_pagination_walks_the_whole_ring_once(sample):
    store = SafetyEventStore()
    for i in range(25):
        store.upsert(ev(sample, i))
    seen, cursor = [], 0
    while True:
        page, nxt = store.query(cursor=cursor, limit=10)
        if not page:
            break
        seen += [e.event_id for e in page]
        cursor = nxt
    assert seen == [f"e{i}" for i in range(25)]
    latest, last = store.query(newest_first=True, limit=3)
    assert [e.event_id for e in latest] == ["e24", "e23", "e22"] and last == 25


def test_summary_counts_only(sample):
    store = SafetyEventStore()
    store.upsert(ev(sample, 1, category=Category.FACE, confidence=0.8, action="masked"))
    store.upsert(ev(sample, 2, category=Category.FACE, confidence=0.6, action="masked"))
    store.upsert(ev(sample, 3, category=Category.UPI_QR, confidence=0.9, action="failsafe"))
    s = store.summary()
    assert s["total"] == 3
    assert s["by_category"]["face"] == {"count": 2, "mean_confidence": 0.7}
    assert s["by_action"] == {"failsafe": 1, "masked": 2}
    assert s["first_ts"] == BASE.replace(second=1).isoformat() and s["last_ts"].endswith("10:00:03+00:00")
    assert store.summary(categories=["upi_qr"])["total"] == 1
    assert SafetyEventStore().summary() == {
        "total": 0, "by_category": {}, "by_action": {}, "by_modality": {}, "first_ts": None, "last_ts": None
    }  # fmt: skip


def test_only_safety_events_are_stored_and_the_contract_blocks_free_text(sample):
    store = SafetyEventStore()
    with pytest.raises(TypeError):
        store.upsert("a chat message with raw text")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        store.upsert(sample("ChatMessage"))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        dataclasses.replace(sample("SafetyEvent"), detail_code="x" * 200)
    with pytest.raises(ValueError):
        dataclasses.replace(sample("SafetyEvent"), detail_code="call 9876543210 now")
    assert len(store) == 0


# --------------------------------------------------------------------------- HTTP
def test_get_events_returns_wire_events_with_no_text_or_image_fields(
    client, auth, publish, wait_until, sample
):
    for i in range(3):
        publish(Topic.SAFETY_EVENT, ev(sample, i))
    assert wait_until(lambda: client.get("/api/events", headers=auth).json()["count"] == 3)
    body = client.get("/api/events", headers=auth).json()
    assert [e["d"]["event_id"] for e in body["events"]] == ["e0", "e1", "e2"] and body["next_cursor"] == 3
    allowed = {f.name for f in dataclasses.fields(SafetyEvent)}
    for e in body["events"]:
        assert e["t"] == "SafetyEvent" and set(e["d"]) == allowed
    dump = json.dumps(body)
    assert "text" not in dump and "image" not in dump and "data:" not in dump


def test_get_events_filters_pages_and_orders(client, auth, publish, wait_until, sample):
    for i in range(6):
        cat = Category.FACE if i % 2 == 0 else Category.PLATE
        publish(Topic.SAFETY_EVENT, ev(sample, i, category=cat))
    assert wait_until(lambda: client.get("/api/events", headers=auth).json()["count"] == 6)
    r = client.get("/api/events", params={"category": "face", "limit": 2}, headers=auth).json()
    assert [e["d"]["event_id"] for e in r["events"]] == ["e0", "e2"]
    r2 = client.get(
        "/api/events", params={"category": "face", "cursor": r["next_cursor"]}, headers=auth
    ).json()
    assert [e["d"]["event_id"] for e in r2["events"]] == ["e4"]
    latest = client.get("/api/events", params={"order": "desc", "limit": 2}, headers=auth).json()
    assert [e["d"]["event_id"] for e in latest["events"]] == ["e5", "e4"]
    since = (BASE + timedelta(seconds=4)).isoformat()
    r3 = client.get("/api/events", params={"since": since}, headers=auth).json()
    assert [e["d"]["event_id"] for e in r3["events"]] == ["e4", "e5"]
    both = client.get(
        "/api/events", params=[("category", "face"), ("category", "plate"), ("limit", 1)], headers=auth
    )
    assert both.json()["count"] == 1
    assert client.get("/api/events", params={"category": "face,plate"}, headers=auth).json()["count"] == 6


@pytest.mark.parametrize(
    "params",
    [{"modality": "nope"}, {"category": "nope"}, {"since": "yesterday"}, {"limit": 0}, {"limit": 5000}, {"cursor": -1}, {"order": "x"}],
)  # fmt: skip
def test_bad_filters_are_422(client, auth, params):
    assert client.get("/api/events", params=params, headers=auth).status_code == 422


def test_upsert_over_the_bus_updates_the_duration(client, auth, publish, wait_until, sample):
    publish(Topic.SAFETY_EVENT, ev(sample, 1))
    assert wait_until(lambda: client.get("/api/events", headers=auth).json()["count"] == 1)
    publish(Topic.SAFETY_EVENT, ev(sample, 1, duration_ms=2500))

    def updated() -> bool:
        events = client.get("/api/events", headers=auth).json()["events"]
        return len(events) == 1 and events[0]["d"]["duration_ms"] == 2500

    assert wait_until(updated)


def test_summary_endpoint(client, auth, publish, wait_until, sample):
    publish(Topic.SAFETY_EVENT, ev(sample, 1, category=Category.FACE))
    publish(Topic.SAFETY_EVENT, ev(sample, 2, category=Category.FACE))
    assert wait_until(lambda: client.get("/api/events/summary", headers=auth).json()["total"] == 2)
    s = client.get("/api/events/summary", params={"category": "face"}, headers=auth).json()
    assert s["by_category"]["face"]["count"] == 2 and set(s) == {
        "total", "by_category", "by_action", "by_modality", "first_ts", "last_ts"
    }  # fmt: skip


def test_roles_streamer_sees_everything_moderator_chat_and_system_only(
    client, auth, role_headers, publish, wait_until, sample
):
    publish(Topic.SAFETY_EVENT, ev(sample, 1, modality=Modality.VIDEO))
    publish(
        Topic.SAFETY_EVENT, ev(sample, 2, modality=Modality.CHAT, category=Category.CHAT_ABUSE, bbox=None)
    )
    publish(
        Topic.SAFETY_EVENT,
        ev(sample, 3, modality=Modality.SYSTEM, category=Category.SYSTEM_STATUS, bbox=None),
    )
    assert wait_until(lambda: client.get("/api/events", headers=auth).json()["count"] == 3)
    mod = role_headers("moderator")
    ids = [e["d"]["event_id"] for e in client.get("/api/events", headers=mod).json()["events"]]
    assert ids == ["e2", "e3"]
    assert client.get("/api/events", params={"modality": "video"}, headers=mod).status_code == 403
    assert client.get("/api/events", params={"modality": "chat"}, headers=mod).json()["count"] == 1
    assert client.get("/api/events/summary", headers=mod).json()["total"] == 2
    for role in ("hotkey", "viewer-readonly"):
        assert client.get("/api/events", headers=role_headers(role)).status_code == 403
        assert client.get("/api/events/summary", headers=role_headers(role)).status_code == 403
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events/summary").status_code == 401
