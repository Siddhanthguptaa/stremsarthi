"""``GET /api/risk``: the strike-guard risk meter as a REST snapshot (the WebSocket streams ``guard.meter``
and ``guard.risk`` live). Streamer only; category, source, score and fixed label codes, nothing else."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ..auth import Principal
from ..deps import require_role

router = APIRouter(prefix="/api")


@router.get("/risk")
async def get_risk(request: Request, _: Principal = Depends(require_role("streamer"))) -> dict[str, Any]:  # noqa: B008
    return request.app.state.risk.snapshot()
