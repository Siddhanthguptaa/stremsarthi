"""Token routes. Tokens are returned once in the body and never logged."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import ROLE_TTL_S, Principal
from ..deps import current_principal, require_role

router = APIRouter(prefix="/api/auth")


@router.get("/whoami")
async def whoami(principal: Principal = Depends(current_principal)) -> dict[str, Any]:  # noqa: B008
    return {"role": principal.role, "session_id": principal.room_id, "expires_at": principal.exp}


def _issue(request: Request, role: str) -> dict[str, Any]:
    ttl = ROLE_TTL_S[role]
    token = request.app.state.tokens.issue(role, ttl)
    return {"token": token, "role": role, "expires_in": ttl}


@router.post("/mod-invite")
async def mod_invite(request: Request, _: Principal = Depends(require_role("streamer"))) -> dict[str, Any]:  # noqa: B008
    return _issue(request, "moderator")


@router.post("/hotkey-token")
async def hotkey_token(request: Request, _: Principal = Depends(require_role("streamer"))) -> dict[str, Any]:  # noqa: B008
    return _issue(request, "hotkey")
