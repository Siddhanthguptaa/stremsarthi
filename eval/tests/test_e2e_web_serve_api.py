"""The browser-test API (eval/e2e_web/serve_api.py): every injected scenario works against the real app, no browser needed."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from eval.e2e_web.serve_api import MARK, build


def test_scenarios_reach_the_real_api(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    app = build(8010, 3010, state_file)
    st = json.loads(state_file.read_text())
    key = {"X-E2E-Key": st["key"]}
    streamer = {"Authorization": f"Bearer {st['streamer']}"}
    mod = {"Authorization": f"Bearer {st['moderator']}"}
    with TestClient(app, base_url="http://127.0.0.1:8010") as c:
        assert c.post("/__e2e/scenario", json={"name": "engine"}).status_code == 403  # no key
        assert c.post("/__e2e/scenario", headers=key, json={"name": "nope"}).status_code == 404
        for name, args in [
            ("engine", {"state": "live"}), ("safety", {"n": 3}), ("risk_signal", {}), ("risk_meter", {"level": "red"}),
            ("caption", {}), ("chat_allowed", {"id": "a1"}), ("chat_hidden", {"id": "h1"}), ("chat_hidden", {"id": "h2"}),
            ("seed_report", {"session_id": "e2e-session-0001"}),
        ]:  # fmt: skip
            r = c.post("/__e2e/scenario", headers=key, json={"name": name, "args": args})
            assert r.status_code == 200, (name, r.text)
        import time

        time.sleep(0.5)
        q = c.get("/api/mod/queue", headers=mod)
        assert q.status_code == 200 and len(q.json()["items"]) == 2
        assert c.get("/api/mod/queue", headers=streamer).status_code == 403
        overlay = c.get("/api/chat/overlay", headers=streamer).json()
        assert MARK not in json.dumps(overlay)
        assert c.get("/api/report/e2e-session-0001", headers=streamer).status_code == 200
        cfg = c.get("/api/destinations", headers=streamer).text
        assert st["fake_yt_key"] not in cfg
