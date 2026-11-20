"""T3.5: the streamer alert feed (GET /api/alerts) and the audio settings (categories + languages)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from contracts import Category, Modality, SafetyEvent, Topic

BASE = datetime(2026, 11, 21, 9, 0, 0, tzinfo=UTC)


def ev(sample, i: int, **over) -> SafetyEvent:
    fields = {
        "event_id": f"a{i}",
        "wall_ts": (BASE + timedelta(seconds=i)).isoformat(),
        "duration_ms": None,
        **over,
    }
    return dataclasses.replace(sample("SafetyEvent"), **fields)


def audio(sample, i: int, category=Category.SPOKEN_PHONE, **over) -> SafetyEvent:
    return ev(sample, i, modality=Modality.AUDIO, category=category, action="muted", bbox=None, **over)


@pytest.fixture
def fed(client: TestClient, publish, sample, wait_until, auth):
    """A client whose event ring holds 3 video, 3 audio and 1 chat event (published on the bus)."""
    events = [ev(sample, i, confidence=0.5 + i / 20) for i in range(1, 4)]
    events += [audio(sample, i, confidence=0.4 + i / 20) for i in range(4, 7)]
    events.append(ev(sample, 7, modality=Modality.CHAT, category=Category.CHAT_ABUSE, bbox=None))
    for e in events:
        publish(Topic.SAFETY_EVENT, e)
    assert wait_until(lambda: len(client.app.state.events) == 7)
    client.headers.update(auth)
    return client


# --- alerts feed -----------------------------------------------------------------------------------


def test_alerts_need_the_streamer_role(client: TestClient, role_headers):
    assert client.get("/api/alerts").status_code == 401
    for role in ("moderator", "hotkey", "viewer-readonly"):
        assert client.get("/api/alerts", headers=role_headers(role)).status_code == 403


def test_alerts_carry_category_time_and_confidence_only(fed: TestClient):
    body = fed.get("/api/alerts").json()
    assert body["count"] == 7
    for alert in body["alerts"]:
        assert set(alert) == {"id", "at", "modality", "category", "action", "confidence", "duration_ms"}
    raw = json.dumps(body)
    for leak in ("bbox", "detail_code", "session_id", "pts", "source"):
        assert leak not in raw


def test_alerts_default_to_newest_first_and_honour_limit(fed: TestClient):
    body = fed.get("/api/alerts", params={"limit": 3}).json()
    assert [a["id"] for a in body["alerts"]] == ["a7", "a6", "a5"]


def test_alerts_filter_by_modality_category_and_confidence(fed: TestClient):
    ids = lambda **p: [a["id"] for a in fed.get("/api/alerts", params=p).json()["alerts"]]  # noqa: E731
    assert ids(modality="audio") == ["a6", "a5", "a4"]
    assert ids(modality=["audio", "chat"], order="asc") == ["a4", "a5", "a6", "a7"]
    assert ids(category="spoken_phone") == ["a6", "a5", "a4"]
    assert ids(min_confidence=0.62, modality="video") == ["a3"]
    assert ids(since=(BASE + timedelta(seconds=6)).isoformat()) == ["a7", "a6"]


def test_alerts_follow_new_events_with_the_cursor(fed: TestClient, publish, sample, wait_until):
    first = fed.get("/api/alerts", params={"order": "asc", "limit": 200}).json()
    cursor = first["next_cursor"]
    publish(Topic.SAFETY_EVENT, audio(sample, 8, category=Category.SPOKEN_UPI))
    assert wait_until(lambda: len(fed.app.state.events) == 8)
    more = fed.get("/api/alerts", params={"order": "asc", "cursor": cursor}).json()
    assert [(a["id"], a["category"]) for a in more["alerts"]] == [("a8", "spoken_upi")]
    assert fed.get("/api/alerts", params={"order": "asc", "cursor": more["next_cursor"]}).json()["count"] == 0


def test_alerts_pick_up_the_duration_when_the_engine_republishes(
    fed: TestClient, publish, sample, wait_until
):
    publish(Topic.SAFETY_EVENT, audio(sample, 4, duration_ms=1800))
    assert wait_until(lambda: fed.get("/api/alerts", params={"limit": 1}).json()["alerts"][0]["id"] == "a4")
    alerts = fed.get("/api/alerts", params={"modality": "audio"}).json()["alerts"]
    assert [(a["id"], a["duration_ms"]) for a in alerts] == [("a4", 1800), ("a6", None), ("a5", None)]


@pytest.mark.parametrize(
    "params",
    [
        {"modality": "smell"},
        {"category": "nope"},
        {"since": "yesterday"},
        {"min_confidence": 2},
        {"limit": 0},
        {"limit": 500},
    ],
)
def test_alerts_reject_bad_filters(fed: TestClient, params):
    assert fed.get("/api/alerts", params=params).status_code == 422


# --- audio toggles and languages (PATCH /api/config) -------------------------------------------------


def test_every_audio_category_can_be_toggled_live(client: TestClient, auth, bus):
    config = client.get("/api/config", headers=auth).json()
    audio_cats = [c for c in config["categories"] if c.startswith("spoken_")]
    assert set(audio_cats) >= {"spoken_phone", "spoken_upi", "spoken_address", "spoken_other_pii"}
    for cat in audio_cats:
        r = client.patch("/api/config", headers=auth, json={"categories": {cat: {"enabled": False}}})
        assert r.status_code == 200 and r.json()["categories"][cat]["enabled"] is False
    changes = [e for t, e in bus.bus.history if t is Topic.CONFIG]
    assert len(changes) == len(audio_cats)
    assert all(
        dict(c.diff) == {f"categories.{cat}.enabled": False}
        for c, cat in zip(changes, audio_cats, strict=True)
    )


def test_public_config_lists_the_supported_languages(client: TestClient, auth):
    audio_cfg = client.get("/api/config", headers=auth).json()["audio"]
    assert audio_cfg["supported_languages"] == ["hi", "en", "kn", "ta", "te", "bn"]
    assert set(audio_cfg["languages"]) <= set(audio_cfg["supported_languages"])


def test_languages_patch_is_deduplicated_published_and_echoed(client: TestClient, auth, bus):
    r = client.patch("/api/config", headers=auth, json={"audio": {"languages": ["ta", "hi", "ta"]}})
    assert r.status_code == 200 and r.json()["audio"]["languages"] == ["ta", "hi"]
    changes = [e for t, e in bus.bus.history if t is Topic.CONFIG]
    assert [dict(c.diff) for c in changes] == [{"audio.languages": ["ta", "hi"]}]


@pytest.mark.parametrize("languages", [[], ["xx"], ["hi", 3], "hi", None])
def test_languages_patch_rejects_unknown_or_empty(client: TestClient, auth, bus, languages):
    before = client.get("/api/config", headers=auth).json()["audio"]["languages"]
    r = client.patch("/api/config", headers=auth, json={"audio": {"languages": languages}})
    assert r.status_code == 422 and r.json()["error"] in {"invalid_config", "invalid_patch"}
    assert [e for t, e in bus.bus.history if t is Topic.CONFIG] == []
    assert client.get("/api/config", headers=auth).json()["audio"]["languages"] == before
