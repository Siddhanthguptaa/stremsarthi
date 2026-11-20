"""Health, token round trip, roles and REQUIRE_AUTH defaults."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.auth import issue_room_token
from app.api.main import create_app
from app.api.settings import ApiSettings
from contracts import CONTRACT_VERSION, Topic


def test_health_is_public_and_shaped(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["bus"] is True
    assert body["engine"] is None
    assert body["contract_version"] == CONTRACT_VERSION
    assert isinstance(body["version"], str)


def test_health_reports_engine_status(client: TestClient, publish, sample, wait_until):
    publish(Topic.ENGINE_STATUS, sample("EngineStatus"))
    assert wait_until(lambda: client.get("/api/health").json()["engine"] is not None)
    engine = client.get("/api/health").json()["engine"]
    assert engine["t"] == "EngineStatus"
    # relayed verbatim from the engine (the sample's version may lag CONTRACT_VERSION by a minor)
    assert engine["d"]["contract_version"] == sample("EngineStatus").contract_version


def test_health_engine_goes_stale(app, settings_factory, bus, publish, sample, wait_until):
    stale_app = create_app(settings_factory(engine_stale_s=0.05), bus)
    with TestClient(stale_app, base_url="http://127.0.0.1:8000") as c:
        c.portal.call(bus.publish, Topic.ENGINE_STATUS, sample("EngineStatus"))  # type: ignore[union-attr]
        assert wait_until(lambda: stale_app.state.hub.last_status is not None)
        assert wait_until(lambda: c.get("/api/health").json()["engine"] is None)


def test_token_round_trip(client: TestClient, app, secret):
    tokens = app.state.tokens
    good = tokens.issue("streamer")
    assert client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {good}"}).status_code == 200

    body, sig = good.split(".")
    tampered = f"{body}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"
    expired = tokens.issue("streamer", ttl_seconds=-5)
    other_room = issue_room_token(secret, "some-other-session", "streamer")
    unknown_role = issue_room_token(secret, tokens.room_id, "admin")
    wrong_key = issue_room_token("a-different-secret", tokens.room_id, "streamer")

    for bad in (tampered, expired, other_room, unknown_role, wrong_key, "garbage", ""):
        r = client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, bad
        assert r.json()["error"] == "unauthorized"


def test_require_auth_defaults_true(client: TestClient, settings_factory):
    assert settings_factory().require_auth is True
    assert client.get("/api/auth/whoami").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_require_auth_false_acts_as_streamer(settings_factory, bus):
    app = create_app(settings_factory(require_auth=False), bus)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        r = c.get("/api/auth/whoami")
        assert r.status_code == 200 and r.json()["role"] == "streamer"
        # a presented but invalid token is still rejected
        assert c.get("/api/auth/whoami", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_roles_and_invites(client: TestClient, auth):
    r = client.post("/api/auth/mod-invite", headers=auth)
    assert r.status_code == 200
    mod = r.json()
    assert mod["role"] == "moderator" and mod["expires_in"] == 6 * 3600
    mod_headers = {"Authorization": f"Bearer {mod['token']}"}
    assert client.get("/api/auth/whoami", headers=mod_headers).json()["role"] == "moderator"
    assert client.post("/api/auth/mod-invite", headers=mod_headers).status_code == 403

    r = client.post("/api/auth/hotkey-token", headers=auth)
    hot = r.json()
    assert hot["role"] == "hotkey" and hot["expires_in"] == 24 * 3600
    hot_headers = {"Authorization": f"Bearer {hot['token']}"}
    assert client.post("/api/auth/hotkey-token", headers=hot_headers).status_code == 403


def test_missing_secret_is_refused():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(ApiSettings(_env_file=None, bus_backend="inprocess"))


def test_unknown_route_uses_error_shape(client: TestClient):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert set(r.json()) == {"error", "detail"}
