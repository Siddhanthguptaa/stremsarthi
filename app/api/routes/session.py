"""Session routes (T1.6 + T6.1 go-live orchestration).

``POST /start`` validates, spawns the engine (and chat when enabled) through ``golive.GoLive`` and
publishes ``SessionCommand``; ``POST /stop`` returns at once while the children are stopped and the
safety report is built in the background; ``GET /health`` reports readiness of every process.
Stream keys are only ever checked for presence here: no response contains one.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from contracts import SessionCommand, Topic

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError

log = logging.getLogger("streamsarthi.api.session")
router = APIRouter(prefix="/api/session")
StreamerOnly = Depends(require_role("streamer"))

KEY_FOR_DESTINATION = {"youtube": "youtube_stream_key", "twitch": "twitch_stream_key"}


class DestinationIn(BaseModel):
    kind: Literal["youtube", "twitch"]
    enabled: bool


class StartBody(BaseModel):
    destinations: list[DestinationIn] = Field(default_factory=list, max_length=4)
    delay_seconds: float | None = Field(None, ge=3.0, le=5.0)
    profile: Literal["dev", "demo"] | None = None
    # Rehearsal: no platform destination at all. The engine still runs the whole chain and publishes only
    # the local ``shielded`` tap (nothing leaves the machine). Needs no stream key.
    rehearsal: bool = False


def _hold(request: Request) -> dict[str, Any]:
    sources = request.app.state.hub.hold_sources_now()
    return {"active": bool(sources), "sources": [s.value for s in sources]}


@router.get("")
async def get_session(request: Request, _: Principal = Depends(require_role("streamer"))) -> dict[str, Any]:  # noqa: B008
    state = request.app.state
    cfg = state.config_store.config
    rec = state.sessions.record
    return {
        "session_id": rec.session_id if rec else None,
        "state": state.sessions.state(state.hub.engine_status()),
        "started_at": rec.started_at if rec else None,
        "delay_seconds": rec.delay_seconds if rec else cfg.delay_seconds,
        "profile": rec.profile if rec else cfg.profile,
        "hold": _hold(request),
        # Per-session ingest URLs and credentials are created by the go-live orchestration (T6.1).
        "ingest": {
            "srt_url": rec.ingest.get("srt_url") if rec else None,
            "rtmp_url": rec.ingest.get("rtmp_url") if rec else None,
        },
        "processes": state.orchestrator.snapshot(),
    }


@router.get("/health")
async def session_health(request: Request, _: Principal = StreamerOnly) -> dict[str, Any]:
    """Readiness of the go-live chain: engine heartbeat, contract version, child processes, chat."""
    state = request.app.state
    engine = state.hub.engine_status()
    procs = state.orchestrator.snapshot()
    session_state = state.sessions.state(engine)
    chat = procs["chat"]
    chat_wanted = state.config_store.config.chat.enabled
    engine_ok = engine is not None and state.hub.contract_ok
    return {
        "state": session_state,
        "ready": engine_ok and session_state in ("waiting_ingest", "live"),
        "engine": {
            "heartbeat": engine is not None,
            "state": engine.state if engine is not None else None,
            "contract_ok": state.hub.contract_ok,
            "age_s": state.hub.engine_age_s(),
        },
        "chat": {
            "enabled": chat_wanted,
            "process": chat["state"] if chat else ("not_started" if chat_wanted else "disabled"),
            "reason": chat["reason"] if chat else None,
            "status_codes": chat["codes"] if chat else [],  # fixed code names only
        },
        "processes": procs,
        "bus": state.bus.connected,
    }


@router.post("/start")
async def start_session(
    request: Request,
    body: StartBody,
    principal: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, Any]:
    state = request.app.state
    store = state.config_store
    cfg = store.config
    engine = state.hub.engine_status()
    if state.sessions.is_active(engine):
        raise ApiError(409, "session_active", "a session is already running")

    enabled = {d.kind: d.enabled for d in body.destinations}
    if body.rehearsal:
        enabled = {"youtube": False, "twitch": False}
    wanted = [kind for kind, on in enabled.items() if on]
    if not wanted and not body.rehearsal:
        raise ApiError(422, "no_destination", "enable at least one destination (youtube or twitch)")
    configured = store.configured_secrets()
    keys = state.keys
    for kind, name in KEY_FOR_DESTINATION.items():  # a key typed into the setup screen counts too
        configured[name] = configured[name] or keys.has(kind)
    missing = [KEY_FOR_DESTINATION[k] for k in wanted if not configured[KEY_FOR_DESTINATION[k]]]
    if missing:  # names only, never values
        raise ApiError(422, "missing_keys", "missing in .env: " + ", ".join(sorted(missing)))

    delay = body.delay_seconds if body.delay_seconds is not None else cfg.delay_seconds
    profile = body.profile or cfg.profile
    new_config = store.with_destinations(enabled)
    if delay != cfg.delay_seconds:
        new_config = new_config.model_copy(update={"delay_seconds": delay})
    # The spawned engine reads its own config: pass what this start changes (no secrets here).
    child_overrides = {
        "STREAM_DELAY_SECONDS": str(delay),
        "DESTINATIONS": json.dumps(
            [d.model_dump(mode="json") for d in new_config.destinations], separators=(",", ":")
        ),
    }
    if state.settings.demo_raw_preview and state.settings.lan_host is None:
        child_overrides["DEMO_RAW_PREVIEW"] = "1"  # environment only, never argv; never with --lan
    try:
        record = await state.golive.begin(
            delay_seconds=delay,
            profile=profile,
            destinations=enabled,
            chat_enabled=new_config.chat.enabled,
            engine_already_running=state.hub.engine_status() is not None,
            extra_env=child_overrides,
        )
    except OSError:
        log.warning("session start: the engine process could not be started")
        raise ApiError(503, "engine_unavailable", "the engine process could not be started") from None
    try:
        if delay != cfg.delay_seconds:
            state.bus.publish(Topic.CONFIG, store.change_for({"delay_seconds": delay}, principal.actor))
        state.bus.publish(Topic.SESSION, SessionCommand(action="start", actor=principal.actor))
    except (RuntimeError, ValueError, TypeError):
        log.warning("session start publish failed")
        await state.orchestrator.stop_all(2.0)
        state.recorder.end()
        state.sessions.discard()
        raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
    store.commit(new_config)
    state.hub.chat.reset_overlay()  # the hidden counter and overlay ring start again
    state.risk.reset()
    return {
        "session_id": record.session_id,
        "state": "starting",
        "checks": {
            "youtube_key": configured["youtube_stream_key"],
            "twitch_key": configured["twitch_stream_key"],
            "groq_key": configured["groq_api_key"],
            "sarvam_key": configured["sarvam_api_key"],
            "gpu": bool(cfg.gpu.enabled),
        },
    }


@router.post("/stop")
async def stop_session(
    request: Request,
    principal: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, Any]:
    state = request.app.state
    try:
        state.bus.publish(Topic.SESSION, SessionCommand(action="stop", actor=principal.actor))
    except (RuntimeError, ValueError, TypeError):
        log.warning("session stop publish failed")
        raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
    state.sessions.stop()
    state.golive.request_stop()  # children stop, the report is built, the state becomes "ended"
    return {"state": "stopping"}


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{6,64}$")


@router.delete("/{session_id}/data")
async def delete_session_data(
    session_id: str,
    request: Request,
    _: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, Any]:
    """Remove ``events.jsonl`` and the report of a finished session."""
    state = request.app.state
    if not _SESSION_ID_RE.match(session_id):
        raise ApiError(404, "not_found", "unknown session")
    cur = state.recorder.current
    if cur is not None and cur.session_id == session_id:
        raise ApiError(409, "session_active", "stop the session before deleting its data")
    if not state.golive.delete_data(session_id):
        raise ApiError(404, "not_found", "no data for that session")
    return {"deleted": True}
