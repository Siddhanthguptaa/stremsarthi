"""``GET /api/pairing`` and ``GET /api/pairing/{rtmp|srt}.png``: streamer only.

The publish login is per session, so pairing exists once a session has been started (409 ``no_session``
before that). Responses are ``Cache-Control: no-store`` (the security middleware) and carry no stream key.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError
from ..pairing import PairingView, build_pairing_info, pairing_payload, public_host, qr_png

log = logging.getLogger("streamsarthi.api.pairing")
router = APIRouter(prefix="/api/pairing")
StreamerOnly = Depends(require_role("streamer"))


def _info(request: Request) -> PairingView:
    state = request.app.state
    rec = state.sessions.record
    if rec is None or rec.ingest_login is None or rec.finished:
        raise ApiError(409, "no_session", "start a session first: the phone login is created per session")
    user, password = rec.ingest_login
    try:
        return build_pairing_info(
            public_host(state.settings.ingest_host),
            user,
            password,
            getattr(state, "pairing_builder", None),
            state.golive.ingest_rtmp_port,
            state.golive.ingest_srt_port,
        )
    except (ValueError, TypeError):
        raise ApiError(
            422, "pairing_unavailable", "pairing is not possible for this ingest address"
        ) from None


@router.get("")
async def get_pairing(request: Request, _: Principal = StreamerOnly) -> dict[str, Any]:
    try:
        return pairing_payload(_info(request))
    except ValueError:
        raise ApiError(422, "pairing_unavailable", "pairing payload is too large") from None


@router.get("/{protocol}.png")
async def get_pairing_qr(
    protocol: Literal["rtmp", "srt"], request: Request, _: Principal = StreamerOnly
) -> Response:
    info = _info(request)
    try:
        text = info.moblin_url(protocol)
        png = await asyncio.to_thread(qr_png, text)
    except ValueError:
        raise ApiError(422, "pairing_unavailable", "pairing payload is too large") from None
    return Response(png, media_type="image/png")
