"""POST/GET /api/hold, GET /api/status: roles, sources, publishing, rate limits, hotkey contract."""

from __future__ import annotations

import statistics
import time

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from contracts import HoldCommand, HoldSource, Topic

ROLES_THAT_HOLD = ("streamer", "moderator", "hotkey")


def post_hold(client: TestClient, headers: dict[str, str], **body: object):
    return client.post("/api/hold", json=body, headers=headers)


# --- auth and roles ------------------------------------------------------------------------------


def test_hold_requires_a_token(client: TestClient, holds):
    assert client.post("/api/hold", json={"active": True}).status_code == 401
    assert client.get("/api/hold").status_code == 401
    assert holds() == []


def test_viewer_readonly_cannot_hold(client: TestClient, role_headers, holds):
    r = post_hold(client, role_headers("viewer-readonly"), active=True)
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    assert holds() == []


@pytest.mark.parametrize(
    ("role", "body", "source"),
    [
        ("streamer", {}, HoldSource.API),
        ("streamer", {"source": "app"}, HoldSource.APP),
        ("streamer", {"source": "api"}, HoldSource.API),
        ("moderator", {}, HoldSource.API),
        ("hotkey", {}, HoldSource.HOTKEY),
        ("hotkey", {"source": "app"}, HoldSource.HOTKEY),  # the role decides, not the body
    ],
)
def test_hold_publishes_one_command_with_the_right_source(
    client: TestClient, role_headers, holds, role, body, source
):
    r = post_hold(client, role_headers(role), active=True, **body)
    assert r.status_code == 202  # accepted: no engine is confirming yet
    cmds = holds()
    assert len(cmds) == 1
    assert cmds[0] == HoldCommand(source=source, active=True, actor=cmds[0].actor)
    assert len(cmds[0].actor) == 16 and "." not in cmds[0].actor  # a hash, never the token
    assert r.json() == {"active": True, "sources": [source.value], "engine": False, "published": 1}


def test_unhold_clears_only_its_own_source(client: TestClient, role_headers, holds, publish, wait_until):
    publish(Topic.HOLD, HoldCommand(source=HoldSource.HOTKEY, active=True, actor="abc"))
    publish(Topic.HOLD, HoldCommand(source=HoldSource.APP, active=True, actor="abc"))
    streamer = role_headers("streamer")
    assert wait_until(
        lambda: client.get("/api/hold", headers=streamer).json()["sources"] == ["app", "hotkey"]
    )

    r = post_hold(client, role_headers("streamer"), active=False, source="app")
    assert r.json() == {"active": True, "sources": ["hotkey"], "engine": False, "published": 1}
    assert holds()[-1] == HoldCommand(source=HoldSource.APP, active=False, actor=holds()[-1].actor)


def test_release_all_is_streamer_only_and_clears_every_user_source(
    client: TestClient, role_headers, holds, publish, wait_until
):
    for src in (HoldSource.HOTKEY, HoldSource.CHAT, HoldSource.APP):
        publish(Topic.HOLD, HoldCommand(source=src, active=True, actor="abc"))
    assert wait_until(
        lambda: len(client.get("/api/hold", headers=role_headers("streamer")).json()["sources"]) == 3
    )

    for role in ("moderator", "hotkey"):
        denied = post_hold(client, role_headers(role), active=False, release_all=True)
        assert denied.status_code == 403
    assert post_hold(client, role_headers("streamer"), active=True, release_all=True).status_code == 403

    before = len(holds())
    r = post_hold(client, role_headers("streamer"), active=False, release_all=True)
    assert r.json()["active"] is False and r.json()["published"] == 3
    released = {c.source for c in holds()[before:]}
    assert released == {HoldSource.HOTKEY, HoldSource.CHAT, HoldSource.APP}
    assert all(c.active is False for c in holds()[before:])


# --- never blocked -------------------------------------------------------------------------------


def test_hold_works_when_idle_and_with_no_engine(client: TestClient, role_headers, holds):
    assert client.get("/api/session", headers=role_headers("streamer")).json()["state"] == "idle"
    r = post_hold(client, role_headers("streamer"), active=True)
    assert r.status_code == 202 and r.json()["engine"] is False
    assert len(holds()) == 1


def test_hold_is_200_when_the_engine_is_reporting(
    client: TestClient, role_headers, publish, sample, wait_until
):
    publish(Topic.ENGINE_STATUS, sample("EngineStatus"))
    assert wait_until(lambda: client.get("/api/health").json()["engine"] is not None)
    r = post_hold(client, role_headers("streamer"), active=True)
    assert r.status_code == 200 and r.json()["engine"] is True


def test_hold_activation_ignores_unexpected_fields_and_unknown_source(
    client: TestClient, role_headers, holds
):
    r = post_hold(client, role_headers("streamer"), active=True, source="from-the-future", extra=[1, 2])
    assert r.status_code == 202
    assert holds()[0].source is HoldSource.API
    assert post_hold(client, role_headers("streamer")).status_code == 422  # but `active` is required


def test_release_is_rate_limited_but_activation_never_is(client: TestClient, role_headers, holds):
    headers = role_headers("streamer")
    limit = client.app.state.settings.hold_release_limit  # type: ignore[attr-defined]
    statuses = [post_hold(client, headers, active=False).status_code for _ in range(limit + 3)]
    assert statuses[:limit] == [202] * limit
    assert statuses[limit:] == [429] * 3
    limited = post_hold(client, headers, active=False)
    assert limited.json()["error"] == "rate_limited"
    published = len(holds())
    assert published == limit  # the refused releases published nothing

    # the same caller is mid-flap and still gets every activation through
    for _ in range(limit * 3):
        assert post_hold(client, headers, active=True).status_code in (200, 202)
    assert len(holds()) == published + limit * 3


def test_rate_limit_is_per_caller(client: TestClient, role_headers):
    limit = client.app.state.settings.hold_release_limit  # type: ignore[attr-defined]
    a, b = role_headers("streamer"), role_headers("hotkey")
    for _ in range(limit + 1):
        post_hold(client, a, active=False)
    assert post_hold(client, a, active=False).status_code == 429
    assert post_hold(client, b, active=False).status_code == 202


def test_publish_failure_is_503_not_a_silent_success(app, bus, role_headers, monkeypatch):
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        monkeypatch.setattr(bus, "publish", lambda *a: (_ for _ in ()).throw(RuntimeError("not started")))
        r = post_hold(c, role_headers("streamer"), active=True)
        assert r.status_code == 503 and r.json()["error"] == "bus_unavailable"


# --- latency -------------------------------------------------------------------------------------


def test_hold_publishes_within_20_ms(client: TestClient, role_headers):
    headers = role_headers("streamer")
    post_hold(client, headers, active=True)  # warm up
    samples = []
    for _ in range(30):
        t0 = time.perf_counter()
        r = post_hold(client, headers, active=True)
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code in (200, 202)
    assert statistics.median(samples) <= 20, f"median {statistics.median(samples):.1f} ms"


# --- GET /api/hold and /api/status ---------------------------------------------------------------


def test_get_hold_follows_the_bus_and_includes_engine_auto_sources(
    client: TestClient, role_headers, publish, sample, wait_until
):
    h = role_headers("hotkey")
    assert client.get("/api/hold", headers=h).json() == {"active": False, "sources": []}
    publish(Topic.HOLD, HoldCommand(source=HoldSource.CHAT, active=True, actor="abc"))
    assert wait_until(lambda: client.get("/api/hold", headers=h).json()["active"])

    status = sample("EngineStatus")
    from dataclasses import replace

    publish(
        Topic.ENGINE_STATUS,
        replace(status, hold_active=True, hold_sources=(HoldSource.AUTO_EGRESS, HoldSource.CHAT)),
    )
    assert wait_until(lambda: "auto_egress" in client.get("/api/hold", headers=h).json()["sources"])
    assert client.get("/api/hold", headers=h).json()["sources"] == ["auto_egress", "chat"]

    publish(Topic.HOLD, HoldCommand(source=HoldSource.CHAT, active=False, actor="abc"))
    assert wait_until(lambda: client.get("/api/hold", headers=h).json()["sources"] == ["auto_egress"])


def test_restarted_api_adopts_the_engines_hold_state(
    client: TestClient, role_headers, publish, sample, wait_until
):
    from dataclasses import replace

    publish(
        Topic.ENGINE_STATUS,
        replace(sample("EngineStatus"), hold_active=True, hold_sources=(HoldSource.HOTKEY,)),
    )
    assert wait_until(
        lambda: client.get("/api/hold", headers=role_headers("streamer")).json()["sources"] == ["hotkey"]
    )


def test_status_shape_and_roles(client: TestClient, role_headers, publish, sample, wait_until):
    r = client.get("/api/status", headers=role_headers("streamer"))
    assert r.status_code == 200
    assert r.json() == {
        "engine": None,
        "engine_age_s": None,
        "hold": {"active": False, "sources": []},
        "bus": {"connected": True, "contract_ok": True},
        "session": {"state": "idle", "session_id": None},
    }
    publish(Topic.ENGINE_STATUS, sample("EngineStatus"))
    assert wait_until(
        lambda: client.get("/api/status", headers=role_headers("moderator")).json()["engine"] is not None
    )
    body = client.get("/api/status", headers=role_headers("streamer")).json()
    assert body["engine"]["t"] == "EngineStatus" and body["engine_age_s"] < 2
    assert client.get("/api/status", headers=role_headers("hotkey")).status_code == 403
    assert client.get("/api/status").status_code == 401


def test_status_marks_a_silent_engine_stale(settings_factory, bus, role_headers, publish, sample, wait_until):
    stale_app = create_app(settings_factory(engine_stale_s=0.05), bus)
    with TestClient(stale_app, base_url="http://127.0.0.1:8000") as c:
        headers = {"Authorization": f"Bearer {stale_app.state.tokens.issue('streamer')}"}
        c.portal.call(bus.publish, Topic.ENGINE_STATUS, sample("EngineStatus"))  # type: ignore[union-attr]
        assert wait_until(lambda: stale_app.state.hub.last_status is not None)
        assert wait_until(lambda: c.get("/api/status", headers=headers).json()["engine"] is None)
        assert c.get("/api/status", headers=headers).json()["engine_age_s"] is not None


# --- hotkey helper contract (docs/specs/ops.md 5.8) ----------------------------------------------


def test_hotkey_token_contract_for_the_windows_helper(client: TestClient, role_headers, holds):
    """The helper sends only Authorization + Content-Type (no Origin), to 127.0.0.1:8000, hold and
    unhold with `{"active": bool}`. The token is hold/unhold scope only, 24 h, written to a file."""
    issued = client.post("/api/auth/hotkey-token", headers=role_headers("streamer")).json()
    assert issued["role"] == "hotkey" and issued["expires_in"] == 24 * 3600
    helper = {"Authorization": f"Bearer {issued['token']}", "Content-Type": "application/json"}

    assert client.post("/api/hold", content='{"active": true}', headers=helper).status_code in (200, 202)
    assert client.post("/api/hold", content='{"active": false}', headers=helper).status_code in (200, 202)
    assert [(c.source, c.active) for c in holds()] == [(HoldSource.HOTKEY, True), (HoldSource.HOTKEY, False)]

    # nothing else
    assert client.get("/api/hold", headers=helper).status_code == 200
    for method, path in (
        ("GET", "/api/config"),
        ("PATCH", "/api/config"),
        ("GET", "/api/status"),
        ("GET", "/api/session"),
        ("POST", "/api/session/start"),
        ("POST", "/api/session/stop"),
        ("POST", "/api/auth/mod-invite"),
        ("POST", "/api/auth/hotkey-token"),
    ):
        r = client.request(method, path, json={}, headers=helper)
        assert r.status_code == 403, (method, path)


def test_hotkey_token_survives_an_api_restart(app, settings_factory, role_headers, bus):
    """The token file outlives the process; a new API instance has a new random room id."""
    old = app.state.tokens.issue("hotkey")
    restarted = create_app(settings_factory(), bus)
    assert restarted.state.tokens.room_id != app.state.tokens.room_id
    with TestClient(restarted, base_url="http://127.0.0.1:8000") as c:
        assert c.post(
            "/api/hold", json={"active": True}, headers={"Authorization": f"Bearer {old}"}
        ).status_code in (200, 202)
        # ...but a streamer token from the old process is not accepted
        stale = app.state.tokens.issue("streamer")
        assert c.get("/api/config", headers={"Authorization": f"Bearer {stale}"}).status_code == 401
        # ...and a hotkey token cannot be turned into a streamer token by editing the role
        assert c.get("/api/config", headers={"Authorization": f"Bearer {old}"}).status_code == 403


def test_hotkey_token_with_wrong_secret_or_expired_is_rejected(app, client: TestClient, secret):
    from app.api.auth import HOTKEY_ROOM, issue_room_token

    wrong_key = issue_room_token("another-secret-entirely", HOTKEY_ROOM, "hotkey")
    expired = issue_room_token(secret, HOTKEY_ROOM, "hotkey", ttl_seconds=-5)
    streamer_in_hotkey_room = issue_room_token(secret, HOTKEY_ROOM, "streamer")  # room does not widen a role
    for bad in (wrong_key, expired, streamer_in_hotkey_room):
        r = client.post("/api/hold", json={"active": True}, headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401
