"""Hold, hold state and the combined status.

Hold is the one control that must always work: no session state, validation of the engine, or rate
limit may stop an *activation*. Releases are rate limited per caller so a stuck key cannot flap the
stream, and a release never clears a hold set by another source.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from contracts import HoldCommand, HoldSource, Topic, to_wire

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError

log = logging.getLogger("streamsarthi.api.control")
router = APIRouter(prefix="/api")

HOLD_ROLES = ("streamer", "moderator", "hotkey")
# Sources a person can set. The engine raises auto_transport and auto_egress itself.
USER_SOURCES = (HoldSource.APP, HoldSource.API, HoldSource.HOTKEY, HoldSource.CHAT)


class HoldBody(BaseModel):
    # Lenient on purpose: an activation must never fail because of an unexpected extra field or an
    # unknown "source" value from an older or third-party client.
    model_config = ConfigDict(extra="ignore")

    active: bool
    # The web app says "app"; any other client (curl, scripts) is "api". The hotkey role is
    # always "hotkey" whatever it sends.
    source: str | None = Field(None, max_length=16)
    # Streamer only, release only: also clear holds set by other user sources (hotkey, chat).
    release_all: bool = False


def source_for(principal: Principal, body: HoldBody) -> HoldSource:
    if principal.role == "hotkey":
        return HoldSource.HOTKEY
    return HoldSource.APP if body.source == "app" else HoldSource.API


def _hold_view(request: Request) -> dict[str, Any]:
    sources = request.app.state.hub.hold_sources_now()
    return {"active": bool(sources), "sources": [s.value for s in sources]}


@router.get("/hold")
async def get_hold(request: Request, _: Principal = Depends(require_role(*HOLD_ROLES))) -> dict[str, Any]:  # noqa: B008
    return _hold_view(request)


@router.post("/hold")
async def post_hold(
    request: Request,
    body: HoldBody,
    response: Response,
    principal: Principal = Depends(require_role(*HOLD_ROLES)),  # noqa: B008
) -> Any:
    state = request.app.state
    hub = state.hub
    if body.release_all and (principal.role != "streamer" or body.active):
        raise ApiError(403, "forbidden", "release_all is for the streamer and only releases")

    commands: list[HoldCommand]
    if body.active:
        # Activation is never rate limited, validated against session state or refused.
        commands = [HoldCommand(source=source_for(principal, body), active=True, actor=principal.actor)]
    else:
        allowed, retry_after = state.hold_release_limiter.check(principal.actor)
        if not allowed:
            raise ApiError(429, "rate_limited", f"too many releases, retry in {retry_after:.0f} s")
        if body.release_all:
            held = [s for s in hub.hold_sources_now() if s in USER_SOURCES]
            commands = [HoldCommand(source=s, active=False, actor=principal.actor) for s in held]
            if not commands:  # nothing recorded as held: still release our own source
                commands = [HoldCommand(source=HoldSource.APP, active=False, actor=principal.actor)]
        else:
            commands = [HoldCommand(source=source_for(principal, body), active=False, actor=principal.actor)]

    expected = set(hub.hold_sources_now())
    try:
        for command in commands:
            state.bus.publish(Topic.HOLD, command)
            if command.active:
                expected.add(command.source)
            else:
                expected.discard(command.source)
    except (RuntimeError, ValueError, TypeError):
        log.warning("hold publish failed")
        raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
    sources = sorted(expected, key=lambda s: s.value)

    engine_up = hub.engine_status() is not None
    if not engine_up:
        response.status_code = 202  # accepted and published, but no engine is confirming
    return {
        "active": bool(sources),
        "sources": [s.value for s in sources],
        "engine": engine_up,
        "published": len(commands),
    }


@router.get("/status")
async def get_status(
    request: Request,
    _: Principal = Depends(require_role("streamer", "moderator")),  # noqa: B008
) -> JSONResponse:
    state = request.app.state
    hub = state.hub
    engine = hub.engine_status()
    age = hub.engine_age_s()
    return JSONResponse(
        {
            "engine": to_wire(engine) if engine is not None else None,
            "engine_age_s": None if age is None else round(age, 2),
            "hold": _hold_view(request),
            "bus": {"connected": state.bus.connected, "contract_ok": hub.contract_ok},
            "session": {
                "state": state.sessions.state(engine),
                "session_id": state.sessions.record.session_id if state.sessions.record else None,
            },
        }
    )
