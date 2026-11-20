"""Log scan for the control endpoints (T1.6): no token, secret or stream key reaches a log, stdout,
stderr or any response body that is not meant to carry it."""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.tests.conftest import FAKE_GROQ_KEY, FAKE_TW_KEY, FAKE_YT_KEY
from contracts import HoldCommand, HoldSource, Topic

H = {"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:8000"}


def test_no_token_secret_or_key_in_logs_or_responses(
    settings_factory, bus, config_with_keys, secret, caplog, capfd, sample
):
    caplog.set_level(logging.DEBUG)
    app = create_app(settings_factory(), bus, config_with_keys)
    tokens = {
        role: app.state.tokens.issue(role) for role in ("streamer", "moderator", "hotkey", "viewer-readonly")
    }
    bad = tokens["streamer"][:-3] + "xyz"
    forged_role = tokens["hotkey"].split(".")[0] + ".AAAA"
    needles = [secret, FAKE_YT_KEY, FAKE_TW_KEY, FAKE_GROQ_KEY, bad, forged_role]
    for token in tokens.values():
        needles += [token, token.split(".")[0], token.split(".")[1]]

    def bearer(role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens[role]}"}

    responses = []
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        c.portal.call(bus.publish, Topic.ENGINE_STATUS, sample("EngineStatus"))  # type: ignore[union-attr]
        c.portal.call(bus.publish, Topic.HOLD, HoldCommand(source=HoldSource.CHAT, active=True, actor="abc"))  # type: ignore[union-attr]
        for role in tokens:
            responses += [
                c.post("/api/hold", json={"active": True}, headers=bearer(role)),
                c.post("/api/hold", json={"active": False, "release_all": True}, headers=bearer(role)),
                c.get("/api/hold", headers=bearer(role)),
                c.get("/api/status", headers=bearer(role)),
                c.get("/api/config", headers=bearer(role)),
                c.patch("/api/config", json={"captions": {"enabled": True}}, headers=bearer(role)),
                c.patch("/api/config", json={"groq_api_key": FAKE_GROQ_KEY}, headers=bearer(role)),
                c.get("/api/session", headers=bearer(role)),
                c.post(
                    "/api/session/start",
                    json={"destinations": [{"kind": "youtube", "enabled": True}]},
                    headers=bearer(role),
                ),
                c.post("/api/session/stop", headers=bearer(role)),
            ]
        responses += [
            c.post(
                "/api/session/start",
                json={"destinations": [{"kind": "youtube", "enabled": True}]},
                headers=bearer("streamer"),
            ),  # 409
            c.post("/api/hold", json={"active": True}, headers={"Authorization": f"Bearer {bad}"}),
            c.post("/api/hold", json={"active": True}, headers={"Authorization": f"Bearer {forged_role}"}),
            c.patch(
                "/api/config",
                json={"categories": {"face": {"sensitivity": FAKE_YT_KEY}}},
                headers=bearer("streamer"),
            ),
            c.get("/api/config", headers={"Host": "evil.example", **bearer("streamer")}),
            c.post(
                "/api/hold",
                json={"active": True},
                headers={"Origin": "http://evil.example", **bearer("streamer")},
            ),
        ]
        for _ in range(12):  # trip the release rate limit too
            responses.append(c.post("/api/hold", json={"active": False}, headers=bearer("streamer")))
        with c.websocket_connect("/api/ws", headers=H) as ws:
            ws.send_text(json.dumps({"type": "auth", "token": bad}))

    assert {r.status_code for r in responses} >= {200, 401, 403, 409, 421, 422, 429}
    captured = caplog.text + "".join(capfd.readouterr())
    for needle in needles:
        assert needle not in captured, "leaked into a log or the console"

    # Response bodies: keys and the signing secret never appear anywhere, tokens only where issued.
    for r in responses:
        for needle in (secret, FAKE_YT_KEY, FAKE_TW_KEY, FAKE_GROQ_KEY):
            assert needle not in r.text
        for token in tokens.values():
            assert token not in r.text


def test_every_log_statement_in_the_api_is_free_of_secret_names():
    """Static guard: no logging call formats a variable called token, secret, key or password."""
    risky = re.compile(r"token|secret|password|passwd|stream_?key|api_?key|bearer|authorization", re.I)
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            is_logger = isinstance(owner, ast.Name) and owner.id in {"log", "logger", "logging"}
            if not is_logger or node.func.attr not in {
                "debug",
                "info",
                "warning",
                "error",
                "exception",
                "critical",
            }:
                continue
            for arg in node.args[1:] if node.args else []:
                if risky.search(ast.unparse(arg)):
                    offenders.append(f"{path.name}:{node.lineno}: {ast.unparse(node)}")
            if (
                node.args
                and isinstance(node.args[0], ast.JoinedStr)
                and risky.search(ast.unparse(node.args[0]))
            ):
                offenders.append(f"{path.name}:{node.lineno}: {ast.unparse(node)}")
    assert offenders == []

    # and nothing prints: the one console line is main() printing the streamer link
    prints = [
        f"{p.name}:{n.lineno}"
        for p in root.glob("*.py")
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"
    ]
    assert {p.split(":")[0] for p in prints} <= {"main.py"}
