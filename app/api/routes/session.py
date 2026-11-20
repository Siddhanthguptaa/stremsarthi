"""Session routes. T1.6 scope: validate, remember and publish ``SessionCommand``.

Spawning the engine, per-session ingest credentials, the report and data deletion arrive in T6.1.
Stream keys are only ever checked for presence here: no response contains one.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from contracts import SessionCommand, Topic

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError

log = logging.getLogger("streamsarthi.api.session")
router = APIRouter(prefix="/api/session")

KEY_FOR_DESTINATION = {"youtube": "youtube_stream_key", "twitch": "twitch_stream_key"}


class DestinationIn(BaseModel):
    kind: Literal["youtube", "twitch"]
    enabled: bool


class StartBody(BaseModel):
    destinations: list[DestinationIn] = Field(default_factory=list, max_length=4)
    delay_seconds: float | None = Field(None, ge=3.0, le=5.0)
    profile: Literal["dev", "demo"] | None = None


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
        "ingest": {"srt_url": None, "rtmp_url": None},
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
    wanted = [kind for kind, on in enabled.items() if on]
    if not wanted:
        raise ApiError(422, "no_destination", "enable at least one destination (youtube or twitch)")
    configured = store.configured_secrets()
    missing = [KEY_FOR_DESTINATION[k] for k in wanted if not configured[KEY_FOR_DESTINATION[k]]]
    if missing:  # names only, never values
        raise ApiError(422, "missing_keys", "missing in .env: " + ", ".join(sorted(missing)))

    delay = body.delay_seconds if body.delay_seconds is not None else cfg.delay_seconds
    profile = body.profile or cfg.profile
    new_config = store.with_destinations(enabled)
    try:
        if delay != cfg.delay_seconds:
            new_config = new_config.model_copy(update={"delay_seconds": delay})
            state.bus.publish(Topic.CONFIG, store.change_for({"delay_seconds": delay}, principal.actor))
        state.bus.publish(Topic.SESSION, SessionCommand(action="start", actor=principal.actor))
    except (RuntimeError, ValueError, TypeError):
        log.warning("session start publish failed")
        raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
    store.commit(new_config)
    state.hub.chat.reset_overlay()  # the hidden counter and overlay ring start again
    record = state.sessions.start(delay, profile, enabled)
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
    return {"state": "stopping"}
