"""GET/PATCH /api/config: masking, validation against contracts.Config, diffs, live limits, atomicity."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tests.conftest import FAKE_GROQ_KEY, FAKE_TW_KEY, FAKE_YT_KEY
from contracts import ConfigChange, Topic


def changes(bus) -> list[ConfigChange]:
    return [e for t, e in bus.bus.history if t is Topic.CONFIG]


@pytest.fixture
def keyed_client(settings_factory, bus, config_with_keys):
    app = create_app(settings_factory(), bus, config_with_keys)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        yield c


def go_live(client: TestClient, publish, sample, wait_until):
    publish(Topic.ENGINE_STATUS, replace(sample("EngineStatus"), state="live"))
    assert wait_until(lambda: client.get("/api/status").json()["session"]["state"] == "live")


# --- GET -----------------------------------------------------------------------------------------


def test_get_requires_the_streamer_role(client: TestClient, role_headers):
    assert client.get("/api/config").status_code == 401
    for role in ("moderator", "hotkey", "viewer-readonly"):
        assert client.get("/api/config", headers=role_headers(role)).status_code == 403


def test_get_returns_the_public_view_and_never_a_secret(keyed_client: TestClient):
    r = keyed_client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["profile"] == "dev" and 3.0 <= body["delay_seconds"] <= 5.0
    assert body["categories"]["face"] == {
        "enabled": True,
        "style": "blur",
        "sensitivity": 0.5,
        "mask_when_unsure": True,
        "padding_px": 12,
    }
    assert body["secrets"]["youtube_stream_key"] == "***"
    assert body["secrets"]["groq_api_key"] == "***"
    assert body["secrets"]["sarvam_api_key"] is None  # not set: shown as absent, not as ***
    for secret in (FAKE_YT_KEY, FAKE_TW_KEY, FAKE_GROQ_KEY):
        assert secret not in r.text
    # engine internals, file paths and private lists are not part of the public view
    for hidden in ("runtime", "relay", "engine", "eval", "vision", "api", "enrol", "ocr"):
        assert hidden not in body
    assert "risky_text_blocklist" not in r.text and "lock_path" not in r.text


def test_private_terms_are_counted_never_listed(settings_factory, bus):
    from contracts import Config

    cfg = Config(
        _env_file=None,
        chat={  # type: ignore[arg-type]
            "streamer_private": {"terms": ["my-street-name", "my-pet-name"], "phones": ["0000000000"]},
            "twitch": {"channel_login": "somechannel"},
        },
    )
    app = create_app(settings_factory(), bus, cfg)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        r = c.get("/api/config", headers={"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
    chat = r.json()["chat"]
    assert chat["streamer_private_counts"] == {"terms": 2, "phones": 1}
    assert chat["twitch"]["channel_configured"] is True
    for private in ("my-street-name", "my-pet-name", "0000000000", "somechannel"):
        assert private not in r.text


# --- PATCH: happy path ---------------------------------------------------------------------------


def test_patch_publishes_only_the_diff(keyed_client: TestClient, bus):
    r = keyed_client.patch("/api/config", json={"categories": {"face": {"enabled": False, "style": "blur"}}})
    assert r.status_code == 200
    assert r.json()["categories"]["face"]["enabled"] is False
    sent = changes(bus)
    assert len(sent) == 1
    assert dict(sent[0].diff) == {"categories.face.enabled": False}  # style was unchanged: not in the diff
    assert len(sent[0].actor) == 16

    assert keyed_client.get("/api/config").json()["categories"]["face"]["enabled"] is False


def test_patch_several_changes_is_one_atomic_change(keyed_client: TestClient, bus):
    patch = {
        "categories": {"aadhaar": {"sensitivity": 0.9}, "plate": {"style": "pixelate"}},
        "captions": {"enabled": True, "target_lang": "ta"},
        "audio": {"languages": ["hi", "en"]},
        "hold": {"retro_seconds": 2.0},
    }
    assert keyed_client.patch("/api/config", json=patch).status_code == 200
    assert len(changes(bus)) == 1
    assert dict(changes(bus)[0].diff) == {
        "categories.aadhaar.sensitivity": 0.9,
        "categories.plate.style": "pixelate",
        "captions.enabled": True,
        "captions.target_lang": "ta",
        "audio.languages": ["hi", "en"],
        "hold.retro_seconds": 2.0,
    }


def test_patch_that_changes_nothing_publishes_nothing(keyed_client: TestClient, bus):
    r = keyed_client.patch("/api/config", json={"categories": {"face": {"enabled": True}}})
    assert r.status_code == 200 and changes(bus) == []


def test_secrets_survive_a_patch(keyed_client: TestClient):
    keyed_client.patch("/api/config", json={"captions": {"enabled": True}})
    assert keyed_client.get("/api/config").json()["secrets"]["twitch_stream_key"] == "***"
    # the stored config still holds the real key (the session start check proves it)
    r = keyed_client.post("/api/session/start", json={"destinations": [{"kind": "twitch", "enabled": True}]})
    assert r.status_code == 200


# --- PATCH: rejected, and nothing happens --------------------------------------------------------


@pytest.mark.parametrize(
    ("patch", "status", "error"),
    [
        ({"bogus": 1}, 422, "not_patchable"),
        ({"categories": {"face": {"bogus": True}}}, 422, "invalid_config"),
        ({"categories": {"not_a_category": {"enabled": True}}}, 422, "invalid_config"),
        ({"categories": {"face": {"sensitivity": 2}}}, 422, "invalid_config"),
        ({"categories": {"face": {"style": "sparkle"}}}, 422, "invalid_config"),
        ({"categories": {"face": 5}}, 422, "invalid_config"),
        ({"captions": {"provider": "indictrans2"}}, 422, "invalid_config"),  # demo-profile extra only
        ({"profile": "demo"}, 422, "not_patchable"),
        ({"groq_api_key": "x"}, 422, "not_patchable"),
        ({"secrets": {"groq_api_key": "x"}}, 422, "not_patchable"),
        ({"youtube_stream_key": "x"}, 422, "not_patchable"),
        ({"destinations": []}, 422, "not_patchable"),
        ({"runtime": {"modules": {"vision": "os:system"}}}, 422, "not_patchable"),
        ({"enrol": {"impl": "os:system"}}, 422, "not_patchable"),
        ({"chat": {"streamer_private": {"terms": ["x"]}}}, 422, "not_patchable"),
        ({"chat": {"streamer_private": {"phones": ["x"]}}}, 422, "not_patchable"),
        ({"chat": {"twitch": {"channel_login": "x"}}}, 422, "not_patchable"),
        ({"guard": {"risky_text_blocklist": ["x"]}}, 422, "not_patchable"),
        ({}, 422, "empty_patch"),
        ({"categories": {}}, 422, "empty_patch"),
        ({"a" * 80: 1}, 422, "invalid_patch"),
        ({"categories.face.enabled": False}, 422, "invalid_patch"),  # dotted keys are not JSON merge
        ({"hold": {"retro_seconds": 99}}, 422, "invalid_config"),
    ],
)
def test_rejected_patch_changes_nothing(keyed_client: TestClient, bus, patch, status, error):
    before = keyed_client.get("/api/config").json()
    r = keyed_client.patch("/api/config", json=patch)
    assert (r.status_code, r.json()["error"]) == (status, error)
    assert set(r.json()) == {"error", "detail"}
    assert changes(bus) == []
    assert keyed_client.get("/api/config").json() == before


def test_a_patch_with_one_bad_key_applies_none_of_it(keyed_client: TestClient, bus):
    before = keyed_client.get("/api/config").json()
    r = keyed_client.patch(
        "/api/config",
        json={"categories": {"face": {"enabled": False}}, "captions": {"enabled": True, "target_lang": 5}},
    )
    assert r.status_code == 422
    assert changes(bus) == [] and keyed_client.get("/api/config").json() == before


def test_validation_errors_never_echo_submitted_values(keyed_client: TestClient):
    r = keyed_client.patch(
        "/api/config", json={"categories": {"face": {"sensitivity": "SUBMITTED-VALUE-123"}}}
    )
    assert r.status_code == 422 and "SUBMITTED-VALUE-123" not in r.text
    r = keyed_client.patch("/api/config", content=b"[1, 2, 3]", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    r = keyed_client.patch("/api/config", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_patch_roles(client: TestClient, role_headers, bus):
    body = {"categories": {"face": {"enabled": False}}}
    assert client.patch("/api/config", json=body).status_code == 401
    for role in ("moderator", "hotkey", "viewer-readonly"):
        assert client.patch("/api/config", json=body, headers=role_headers(role)).status_code == 403
    assert changes(bus) == []


def test_failed_publish_applies_nothing(settings_factory, bus, config_with_keys, monkeypatch):
    app = create_app(settings_factory(), bus, config_with_keys)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.headers.update({"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"})
        monkeypatch.setattr(bus, "publish", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
        r = c.patch("/api/config", json={"categories": {"face": {"enabled": False}}})
        assert r.status_code == 503 and r.json()["error"] == "bus_unavailable"
        assert c.get("/api/config").json()["categories"]["face"]["enabled"] is True


# --- live restrictions ---------------------------------------------------------------------------


def test_delay_gpu_and_detect_need_a_restart_while_live(
    keyed_client: TestClient, bus, publish, sample, wait_until
):
    go_live(keyed_client, publish, sample, wait_until)
    for patch in (
        {"delay_seconds": 3.5},
        {"gpu": {"mem_limit_mb": 2000}},
        {"detect": {"stride": 2}},
        {"delay_seconds": 3.5, "categories": {"face": {"enabled": False}}},  # mixed: refused as a whole
    ):
        r = keyed_client.patch("/api/config", json=patch)
        assert r.status_code == 409, patch
        assert r.json()["error"] == "requires_restart"
    assert changes(bus) == []
    assert keyed_client.get("/api/config").json()["categories"]["face"]["enabled"] is True


def test_live_changeable_keys_still_work_while_live(
    keyed_client: TestClient, bus, publish, sample, wait_until
):
    go_live(keyed_client, publish, sample, wait_until)
    r = keyed_client.patch(
        "/api/config",
        json={"categories": {"face": {"enabled": False}}, "chat": {"commands": {"enabled": False}}},
    )
    assert r.status_code == 200
    assert dict(changes(bus)[0].diff) == {"categories.face.enabled": False, "chat.commands.enabled": False}


def test_delay_can_change_while_idle(keyed_client: TestClient, bus):
    r = keyed_client.patch("/api/config", json={"delay_seconds": 3.5})
    assert r.status_code == 200 and r.json()["delay_seconds"] == 3.5
    assert dict(changes(bus)[0].diff) == {"delay_seconds": 3.5}
    assert keyed_client.patch("/api/config", json={"delay_seconds": 5.5}).status_code == 422
    assert keyed_client.patch("/api/config", json={"delay_seconds": 2.0}).status_code == 422


def test_high_delay_reports_the_warning(keyed_client: TestClient):
    r = keyed_client.patch("/api/config", json={"delay_seconds": 4.8})
    assert r.status_code == 200 and "4.3" in r.json()["delay_warning"]
