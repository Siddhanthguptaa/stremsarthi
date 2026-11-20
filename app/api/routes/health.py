"""``GET /api/health``: unauthenticated liveness plus bus and engine state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from contracts import CONTRACT_VERSION, to_wire

from .. import API_VERSION

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    status = state.hub.engine_status()
    return {
        "status": "ok",
        "bus": state.bus.connected,
        "engine": to_wire(status) if status is not None else None,
        "contract_ok": state.hub.contract_ok,
        "version": API_VERSION,
        "contract_version": CONTRACT_VERSION,
    }
