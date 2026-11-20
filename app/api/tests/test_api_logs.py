"""No token, secret or key appears in logs or error bodies."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient


def test_no_secret_or_token_in_logs(client: TestClient, app, secret, caplog):
    caplog.set_level(logging.DEBUG)
    token = app.state.tokens.issue("streamer")
    bad = token[:-3] + "xyz"
    responses = [
        client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {token}"}),
        client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {bad}"}),
        client.post(
            "/api/auth/mod-invite",
            headers={"Authorization": f"Bearer {token}", "Origin": "http://evil.example"},
        ),
        client.get("/api/health", headers={"Host": "evil.example"}),
    ]
    with client.websocket_connect(
        "/api/ws", headers={"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:8000"}
    ) as ws:
        ws.send_text(f'{{"type":"auth","token":"{bad}"}}')
    invite = client.post("/api/auth/mod-invite", headers={"Authorization": f"Bearer {token}"}).json()["token"]

    blob = caplog.text
    for needle in (secret, token, bad, invite, token.split(".")[1]):
        assert needle not in blob
    for r in responses:
        assert secret not in r.text and token.split(".")[1] not in r.text
