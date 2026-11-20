"""``GET /api/chat/overlay``: the streamer's chat overlay snapshot.

Only what the streamer was already allowed to see: the number of hidden messages and the recent messages
whose verdict was ALLOW. The text of a hidden, held, deleted or timed-out message is not in this API
response and cannot be, because the streamer role has no path to it (``/api/mod/*`` is moderator only and
``chat.modqueue`` is not a streamer topic). Live messages arrive on the WebSocket (``chat.allowed``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import Principal
from ..deps import require_role

router = APIRouter(prefix="/api/chat")


@router.get("/overlay")
async def get_overlay(request: Request, _: Principal = Depends(require_role("streamer"))) -> dict[str, Any]:  # noqa: B008
    return request.app.state.hub.chat.overlay_snapshot()
