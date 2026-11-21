"""The real Stream Sarthi API for the browser tests (T6.5), with an in-process bus and no engine.

    python -m eval.e2e_web.serve_api --port 8010 --web-port 3010 --state-file /tmp/ss-e2e-state.json

What is real: ``app.api.main.create_app`` with every route, the role checks, the WebSocket hub, the
mod-queue state, the report builder and the privacy guard. What is faked: the bus is the in-process
bus (no ZeroMQ, so it never touches the running demo stack's sockets), ``SESSION_SPAWN`` is off (no
engine or chat process is ever started) and the face enroller is ``contracts.fakes.FakeEnroller``.

The test runner needs events that an engine would publish. They are injected through one extra route,
``POST /__e2e/scenario`` (header ``X-E2E-Key`` = the key in the state file; this module adds it to the
app object only, never to ``app/api``). The state file also carries a streamer token and a moderator
token, so the tests never need the console link. Nothing here reads ``.env``: the config is built with
``_env_file=None`` and a visible FAKE stream key, which is what lets a test prove the key never
reaches the page.
"""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from app.api.bus import InProcessBusClient
from app.api.main import create_app
from app.api.settings import ApiSettings
from contracts import (
    CaptionEvent,
    Category,
    ChatAction,
    ChatMessage,
    ChatVerdict,
    Config,
    EngineStatus,
    Modality,
    ModQueueItem,
    Quad,
    RiskMeter,
    RiskSignal,
    SafetyEvent,
    Topic,
)
from contracts.fakes.vision import FakeEnroller

FAKE_YT_KEY = "yt-SPECIMEN-FAKE-KEY-0a1b2c3d"
FAKE_TW_KEY = "tw-SPECIMEN-FAKE-KEY-9z8y7x6w"
MARK = "E2E-HIDDEN-TEXT"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def engine_status(**kw: Any) -> EngineStatus:
    base: dict[str, Any] = {
        "state": "live", "hold_active": False, "hold_sources": (), "delay_s_p50": 4.1, "fps": 29.5,
        "cpu_cores": 1.2, "vram_mb": 900, "allowlist_version": 0, "lanes_stale": (),
        "protect_seconds": 0.0, "contract_version": "1.1.3",
    }  # fmt: skip
    base.update(kw)
    return EngineStatus(**base)


def safety_event(
    i: int, category: Category = Category.FACE, modality: Modality = Modality.VIDEO, action: str = "masked"
) -> SafetyEvent:
    return SafetyEvent(
        event_id=f"e2e{i:06d}", session_id="e2e-engine", wall_ts=(datetime.now(UTC) + timedelta(milliseconds=i)).isoformat(),
        pts=float(i), modality=modality, category=category, action=action,  # type: ignore[arg-type]
        confidence=0.6 + (i % 4) / 10,
        bbox=Quad.from_xyxy(10, 20, 110, 120) if modality is Modality.VIDEO else None,
        duration_ms=900 if modality is Modality.AUDIO else None, source="e2e", detail_code="ok",
    )  # fmt: skip


def chat_message(
    mid: str, text: str, platform: str = "twitch", author: str = "SPECIMEN viewer"
) -> ChatMessage:
    return ChatMessage(
        platform=platform,  # type: ignore[arg-type]
        message_id=mid, channel_id="chan1", author_id=f"author-{mid}", author_display=author,
        text=text, received_at=datetime.now(UTC).timestamp(), is_broadcaster=False, is_moderator=False,
    )  # fmt: skip


def chat_verdict(
    mid: str, action: ChatAction, platform: str = "twitch", cat: Category = Category.CHAT_ABUSE
) -> ChatVerdict:
    return ChatVerdict(
        platform=platform,  # type: ignore[arg-type]
        message_id=mid, action=action, categories=() if action is ChatAction.ALLOW else (cat,),
        confidence=0.93, reason="rules_abuse", stage="rules", latency_ms=1.0, timeout_seconds=None,
        platform_result="pending",
    )  # fmt: skip


async def run_scenario(app: Any, name: str, a: dict[str, Any]) -> dict[str, Any]:
    bus: InProcessBusClient = app.state.bus
    pub = bus.publish
    out: dict[str, Any] = {}
    if name == "engine":
        pub(
            Topic.ENGINE_STATUS,
            engine_status(**{k: tuple(v) if isinstance(v, list) else v for k, v in a.items()}),
        )
    elif name == "safety":
        cats = [Category.FACE, Category.UPI_QR, Category.AADHAAR, Category.PLATE]
        for i in range(int(a.get("n", 3))):
            pub(Topic.SAFETY_EVENT, safety_event(int(a.get("start", 0)) + i, cats[i % len(cats)]))
    elif name == "risk_signal":
        sig = RiskSignal(
            wall_ts=_now(), pts=None, source=a.get("source", "music"), category=Category.MUSIC,
            score=float(a.get("score", 0.8)), ttl_s=float(a.get("ttl_s", 30.0)), label=a.get("label", "fp:demo_03"),
        )  # fmt: skip
        pub(Topic.RISK, sig)
    elif name == "risk_meter":
        pub(
            Topic.RISK_METER,
            RiskMeter(
                wall_ts=_now(),
                level=a.get("level", "red"),
                score=float(a.get("score", 0.9)),
                top_sources=("music",),
            ),
        )
    elif name == "caption":
        pub(
            Topic.CAPTION,
            CaptionEvent(
                pts_start=1.0,
                pts_end=2.0,
                lang=a.get("lang", "hi"),
                text=a.get("text", "SPECIMEN caption line"),
                is_final=True,
            ),
        )
    elif name == "chat_allowed":
        mid = a.get("id", secrets.token_hex(3))
        msg = chat_message(mid, a.get("text", "namaste everyone"))
        pub(Topic.CHAT_ALLOWED, msg)
        pub(Topic.CHAT_VERDICT, chat_verdict(mid, ChatAction.ALLOW))
    elif name == "chat_hidden":
        mid = a.get("id", secrets.token_hex(3))
        msg = chat_message(
            mid,
            a.get("text", f"{MARK}-{mid}"),
            a.get("platform", "twitch"),
            a.get("author", "SPECIMEN viewer"),
        )
        v = chat_verdict(mid, ChatAction.HOLD_FOR_MOD, a.get("platform", "twitch"))
        pub(Topic.CHAT_VERDICT, v)
        pub(Topic.CHAT_MODQUEUE, ModQueueItem(msg, v, datetime.now(UTC).timestamp(), "pending"))
    elif name == "seed_report":
        sid = a.get("session_id", "e2e-session-0001")
        rec = app.state.recorder
        rec.begin(sid, (datetime.now(UTC) - timedelta(minutes=20)).isoformat(), "dev", 4.0)
        cats = [
            (Category.FACE, Modality.VIDEO, "masked"),
            (Category.UPI_QR, Modality.VIDEO, "masked"),
            (Category.SPOKEN_PHONE, Modality.AUDIO, "muted"),
            (Category.CHAT_ABUSE, Modality.CHAT, "hidden"),
            (Category.MUSIC, Modality.GUARD, "warned"),
        ]
        for i in range(int(a.get("n", 40))):
            c, m, act = cats[i % len(cats)]
            rec.observe(Topic.SAFETY_EVENT, safety_event(i, c, m, act))
        ended = rec.end()
        if ended is not None:
            app.state.golive._store(ended)  # what a finished session does; the report is then served
        out["session_id"] = sid
    else:
        raise HTTPException(404, f"unknown scenario {name}")
    return out


def build(port: int, web_port: int, state_file: Path) -> Any:
    sessions = Path(tempfile.mkdtemp(prefix="ss-e2e-sessions-"))
    settings = ApiSettings(  # type: ignore[call-arg]
        _env_file=None, secret_key=secrets.token_urlsafe(32), bus_backend="inprocess", session_spawn=False,
        sessions_dir=str(sessions), port=port, web_port=web_port,
        allowed_origins=[f"http://127.0.0.1:{web_port}", f"http://localhost:{web_port}"],
        hold_release_limit=100, mod_action_limit=500, enrol_rate_limit=100,
    )  # fmt: skip
    config = Config(_env_file=None, youtube_stream_key=FAKE_YT_KEY, twitch_stream_key=None)  # type: ignore[arg-type]
    app = create_app(settings, InProcessBusClient(), config)
    app.state.enroller = FakeEnroller()
    key = secrets.token_urlsafe(16)

    @app.post("/__e2e/scenario")
    async def scenario(request: Request) -> dict[str, Any]:
        if request.headers.get("x-e2e-key") != key:
            raise HTTPException(403, "no")
        body = await request.json()
        return await run_scenario(request.app, str(body["name"]), dict(body.get("args") or {}))

    state = {
        "api": f"http://127.0.0.1:{port}", "web": f"http://127.0.0.1:{web_port}", "key": key,
        "streamer": app.state.tokens.issue("streamer"), "moderator": app.state.tokens.issue("moderator"),
        "fake_yt_key": FAKE_YT_KEY, "mark": MARK,
    }  # fmt: skip
    state_file.write_text(json.dumps(state))
    state_file.chmod(0o600)
    return app


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--web-port", type=int, default=3010)
    p.add_argument("--state-file", default="/tmp/ss-e2e-state.json")
    args = p.parse_args()
    app = build(args.port, args.web_port, Path(args.state_file))
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
