"""Destinations (YouTube, Twitch) and their stream keys.

A key may come from ``.env`` (read by the engine) or be typed into the setup screen: it is then held in
RAM only (``keystore.DestinationKeys``), handed to the engine child through its environment at go-live,
and forgotten when the API stops. No endpoint ever returns a key: only ``configured`` and where it comes
from. Streamer only.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError
from ..keystore import InvalidKey

router = APIRouter(prefix="/api/destinations")
StreamerOnly = Depends(require_role("streamer"))
KEY_FIELD = {"youtube": "youtube_stream_key", "twitch": "twitch_stream_key"}


class KeyBody(BaseModel):
    key: SecretStr = Field(..., min_length=8, max_length=200)


def _view(request: Request) -> list[dict[str, Any]]:
    state = request.app.state
    cfg = state.config_store.config
    out = []
    for d in cfg.destinations:
        if d.kind not in KEY_FIELD:
            continue
        in_env = getattr(cfg, KEY_FIELD[d.kind]) is not None
        in_ram = state.keys.has(d.kind)
        out.append(
            {
                "kind": d.kind,
                "enabled": d.enabled,
                "configured": in_env or in_ram,
                "key_source": "session" if in_ram else ("env" if in_env else None),
            }
        )
    return out


@router.get("")
async def list_destinations(request: Request, _: Principal = StreamerOnly) -> dict[str, Any]:
    return {"destinations": _view(request)}


@router.put("/{kind}/key")
async def set_key(
    kind: Literal["youtube", "twitch"], body: KeyBody, request: Request, _: Principal = StreamerOnly
) -> dict[str, Any]:
    state = request.app.state
    if state.sessions.is_active(state.hub.engine_status()):
        raise ApiError(409, "session_active", "change the stream key before going live")
    try:
        state.keys.set(kind, body.key.get_secret_value())
    except InvalidKey:
        raise ApiError(422, "invalid_key", "that does not look like a stream key") from None
    return {"kind": kind, "configured": True, "key_source": "session"}


@router.delete("/{kind}/key")
async def clear_key(
    kind: Literal["youtube", "twitch"], request: Request, _: Principal = StreamerOnly
) -> dict[str, Any]:
    removed = request.app.state.keys.clear(kind)
    return {"kind": kind, "cleared": removed}
