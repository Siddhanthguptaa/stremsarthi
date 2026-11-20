"""Host/Origin checks, headers, CSP, CORS, body limits, bind rules."""

from __future__ import annotations

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app.api.main import create_app
from app.api.security import CSP, hostname_of
from app.api.settings import ApiSettings


def test_bad_host_is_421(app):
    with TestClient(app, base_url="http://evil.example") as c:
        r = c.get("/api/health")
        assert r.status_code == 421
        assert r.json()["error"] == "bad_host"


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST", "[::1]:8000"])
def test_allowed_hosts(app, host):
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/api/health", headers={"Host": host}).status_code == 200


def test_bad_origin_on_post_is_403(client, auth, origin):
    r = client.post("/api/auth/mod-invite", headers={**auth, "Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "bad_origin"
    r = client.post("/api/auth/mod-invite", headers={**auth, "Origin": origin})
    assert r.status_code == 200
    # no Origin (non-browser client such as the hotkey helper): allowed, the token protects it
    assert client.post("/api/auth/mod-invite", headers=auth).status_code == 200
    # a look-alike origin is not an exact match
    r = client.post("/api/auth/mod-invite", headers={**auth, "Origin": origin + ".evil.example"})
    assert r.status_code == 403


def test_ws_origin_is_enforced(client):
    for headers in ({"origin": "http://evil.example"}, {}):
        headers = {**headers, "host": "127.0.0.1:8000"}
        with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)):
            with client.websocket_connect("/api/ws", headers=headers):
                pass


def test_security_headers_on_every_response(client, auth):
    ok = client.get("/api/health")
    denied = client.get("/api/auth/whoami")
    bad_host = client.get("/api/health", headers={"Host": "evil.example"})
    for r in (ok, denied, bad_host):
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["content-security-policy"] == CSP
    assert "script-src" not in CSP and "default-src 'none'" in CSP and "frame-ancestors 'none'" in CSP


def test_cors_allows_only_configured_origins(client, origin):
    pre = client.options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert pre.headers.get("access-control-allow-origin") == origin
    assert "authorization" in pre.headers.get("access-control-allow-headers", "").lower()
    assert pre.headers.get("access-control-allow-origin") != "*"
    evil = client.options(
        "/api/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in evil.headers


def test_body_limits(client, auth):
    big = b"x" * (2 * 1024 * 1024 + 1)
    r = client.post("/api/auth/mod-invite", headers=auth, content=big)
    assert r.status_code == 413 and r.json()["error"] == "payload_too_large"


def test_body_limit_on_chunked_upload(app):
    @app.post("/api/_sink")
    async def sink(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    def chunks(n: int):
        for _ in range(n):
            yield b"y" * (1024 * 1024)

    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.post("/api/_sink", content=chunks(1)).json() == {"bytes": 1024 * 1024}
        r = c.post("/api/_sink", content=chunks(3))  # no Content-Length: counted while streaming
        assert r.status_code == 413 and r.json()["error"] == "payload_too_large"


def test_hostname_parsing():
    assert hostname_of("127.0.0.1:8000") == "127.0.0.1"
    assert hostname_of("[::1]:8000") == "[::1]"
    assert hostname_of("LocalHost") == "localhost"


@pytest.mark.parametrize("bad", ["0.0.0.0", "::", ""])
def test_never_binds_wildcard(bad):
    with pytest.raises(ValueError):
        ApiSettings(_env_file=None, secret_key="x", host=bad)
    with pytest.raises(ValueError):
        ApiSettings(_env_file=None, secret_key="x", lan_host=bad)


def test_lan_mode_forces_auth_and_allows_the_lan_host(settings_factory, bus):
    s = settings_factory(lan_host="192.168.1.20", require_auth=False)
    assert s.require_auth is True and s.bind_host == "192.168.1.20"
    assert "192.168.1.20" in s.allowed_hosts
    assert "http://192.168.1.20:3000" in s.allowed_origins
    app = create_app(s, bus)
    with TestClient(app, base_url="http://192.168.1.20:8000") as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/auth/whoami").status_code == 401


def test_env_lists_are_comma_separated(monkeypatch):
    monkeypatch.setenv("API_ALLOWED_ORIGINS", "http://a.test:3000, http://b.test:3000")
    s = ApiSettings(_env_file=None, secret_key="x")
    assert s.allowed_origins == ["http://a.test:3000", "http://b.test:3000"]
