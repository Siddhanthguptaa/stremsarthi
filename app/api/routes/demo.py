"""Demo preview routes: ``POST /api/demo/preview-token``, ``GET /api/demo/preview/{raw|protected}.mjpg``,
``GET /api/demo/status``. See ``app/api/demo_preview.py`` for the ffmpeg lifecycle and the privacy notes.

The stream routes are authorised by a short-lived *preview* token in ``?t=`` (an ``<img>`` tag cannot send
a header). That token verifies nowhere else. Nothing here logs a token, a URL or a frame.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..auth import PREVIEW_TTL_S, Principal
from ..demo_preview import MEDIA_TYPE, NO_CACHE_HEADERS, PreviewError, Stream
from ..deps import require_role
from ..errors import ApiError

router = APIRouter(prefix="/api/demo")
StreamerOnly = Depends(require_role("streamer"))
LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


def _loopback_only(request: Request) -> bool:
    """True when the API is bound to loopback (no ``--lan``) and this request came from loopback."""
    return request.app.state.settings.lan_host is None and _client_is_loopback(request)


def _client_is_loopback(request: Request) -> bool:
    return request.client is not None and request.client.host in LOOPBACK_CLIENTS


def _bound_to_loopback(request: Request) -> bool:
    return request.app.state.settings.lan_host is None


@router.post("/preview-token")
async def preview_token(request: Request, _: Principal = StreamerOnly) -> dict[str, Any]:
    return {"token": request.app.state.tokens.issue_preview(PREVIEW_TTL_S), "expires_in_s": PREVIEW_TTL_S}


@router.get("/status")
async def demo_status(request: Request, _: Principal = StreamerOnly) -> dict[str, Any]:
    state = request.app.state
    hub = state.demo_preview
    raw_enabled = bool(state.settings.demo_raw_preview) and _bound_to_loopback(request)
    out: dict[str, bool] = {}
    for stream in ("protected", "raw"):
        try:
            hub.check_session(stream)
            out[stream] = True
        except PreviewError:
            out[stream] = False
    return {
        "raw_enabled": raw_enabled,
        "protected_available": out["protected"],
        "raw_available": raw_enabled and out["raw"],
        "clients": hub.clients(),
        "max_clients": hub.max_clients,
    }


@router.get("/preview/{stream}.mjpg")
async def preview_stream(
    stream: Literal["raw", "protected"], request: Request, t: str | None = None
) -> StreamingResponse:
    state = request.app.state
    if not state.tokens.verify_preview(t):
        raise ApiError(401, "unauthorized", "missing or invalid preview token")
    kind: Stream = stream
    if kind == "raw":
        # The raw view is unmasked video: loopback only, and only when the operator turned it on.
        if not _loopback_only(request):
            raise ApiError(403, "raw_preview_loopback_only", "the raw preview is served to loopback only")
        if not state.settings.demo_raw_preview:
            raise ApiError(403, "raw_preview_disabled", "set DEMO_RAW_PREVIEW=1 to enable the raw preview")
    hub = state.demo_preview
    try:
        lease = await hub.acquire(kind, request.client.host if request.client else "")
    except PreviewError as exc:
        raise ApiError(exc.status, exc.code, exc.detail, exc.headers) from None
    return StreamingResponse(
        hub.frames(lease),
        media_type=MEDIA_TYPE,
        headers=NO_CACHE_HEADERS,
        background=BackgroundTask(lease.release),  # also covers a client that never read a byte
    )
