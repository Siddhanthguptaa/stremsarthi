"""Moderator console endpoints: the queue snapshot and moderator actions.

Role gate: ``moderator`` only. The streamer is refused on purpose (the streamer must never see the text of
a held message). The queue shown here is the API's own copy, rebuilt from the ``chat.*`` bus events
(``chat_state.py``); the chat process owns the real queue and does the work. An action is validated against
that copy, published as a ``ModAction`` on ``chat.modaction`` and answered by the chat process with a
follow-up ``chat.verdict`` (``platform_result``) and a republished ``chat.modqueue`` item.

Privacy: message text appears only in the ``GET /api/mod/queue`` body (moderator role). It is never logged
and no error body repeats it. Validation errors name the field, not the value.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts import ModAction, Topic

from ..auth import Principal
from ..chat_state import ActionRefused
from ..deps import require_role
from ..errors import ApiError

log = logging.getLogger("streamsarthi.api.mod")
router = APIRouter(prefix="/api/mod")

MAX_TIMEOUT_S = 1_209_600  # Twitch's maximum (14 days); YouTube accepts any shorter duration
ModOnly = Depends(require_role("moderator"))


class ModActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=128)
    platform: Literal["youtube", "twitch"]
    action: Literal["approve", "delete", "timeout", "dismiss"]
    timeout_seconds: int | None = Field(None, ge=1, le=MAX_TIMEOUT_S)

    @model_validator(mode="after")
    def _timeout_matches_action(self) -> ModActionBody:
        if self.action == "timeout" and self.timeout_seconds is None:
            raise ValueError("timeout_seconds is required for a timeout")
        if self.action != "timeout" and self.timeout_seconds is not None:
            raise ValueError("timeout_seconds is only for a timeout")
        return self


@router.get("/queue")
async def get_queue(request: Request, _: Principal = ModOnly) -> dict[str, Any]:
    """Pending items (with text, newest last), recently resolved stubs (no text) and the counters."""
    state = request.app.state
    return state.hub.chat.mod_snapshot(as_of_seq=state.hub.seq)


@router.post("/action")
async def post_action(
    request: Request, body: ModActionBody, principal: Principal = ModOnly
) -> dict[str, Any]:
    state = request.app.state
    allowed, retry_after = state.mod_action_limiter.check(principal.actor)
    if not allowed:
        raise ApiError(429, "rate_limited", f"too many actions, retry in {retry_after:.0f} s")
    chat = state.hub.chat
    try:
        chat.check_action(body.platform, body.message_id, body.action)
    except ActionRefused as refused:
        raise ApiError(refused.status, refused.code, refused.detail) from None
    action = ModAction(
        message_id=body.message_id,
        platform=body.platform,
        action=body.action,
        timeout_seconds=body.timeout_seconds,
        actor=principal.actor,
    )
    try:
        state.bus.publish(Topic.CHAT_MODACTION, action)
    except (RuntimeError, ValueError, TypeError):
        log.warning("moderator action publish failed")
        raise ApiError(503, "bus_unavailable", "could not reach the chat shield") from None
    chat.mark_requested(body.platform, body.message_id, body.action)
    return {"accepted": True, "action": body.action}
