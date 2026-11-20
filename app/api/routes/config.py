"""``GET`` and ``PATCH /api/config`` (streamer only). Rules are in ``config_store.py``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request

from contracts import Topic

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError

log = logging.getLogger("streamsarthi.api.config")
router = APIRouter(prefix="/api")


@router.get("/config")
async def get_config(request: Request, _: Principal = Depends(require_role("streamer"))) -> dict[str, Any]:  # noqa: B008
    return request.app.state.config_store.public()


@router.patch("/config")
async def patch_config(
    request: Request,
    patch: dict[str, Any] = Body(...),  # noqa: B008
    principal: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, Any]:
    # No await between validation and commit: the whole patch applies, or none of it does.
    state = request.app.state
    store = state.config_store
    live = state.sessions.is_active(state.hub.engine_status())
    new_config, diff = store.plan_patch(patch, live)
    if diff:
        change = store.change_for(diff, principal.actor)
        try:
            state.bus.publish(Topic.CONFIG, change)
        except (RuntimeError, ValueError, TypeError):
            log.warning("config publish failed")
            raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
        store.commit(new_config)
    return store.public()
