"""``GET /api/alerts``: the streamer's live alert feed, read from the SafetyEvent ring.

An alert is the privacy-reduced view of a ``SafetyEvent``: category, modality, action, time, confidence
and (when the engine knows it) how long the mask or mute lasted. The box, the source and detail codes,
the pts and the session id are dropped, so the streamer UI has nothing but "what, when, how sure".
Streamer only: moderators read chat events through ``/api/events``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from contracts import SafetyEvent

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError
from ..events import parse_ts
from .events import _CATEGORY_VALUES, _MODALITY_VALUES, _csv

router = APIRouter(prefix="/api")

MAX_ALERTS = 200
StreamerOnly = Depends(require_role("streamer"))


def alert_view(event: SafetyEvent) -> dict[str, Any]:
    """The only fields that leave the API for the alert feed (allowlist, not a blocklist)."""
    return {
        "id": event.event_id,
        "at": event.wall_ts,
        "modality": event.modality.value,
        "category": event.category.value,
        "action": event.action,
        "confidence": round(event.confidence, 3),
        "duration_ms": event.duration_ms,
    }


@router.get("/alerts")
async def list_alerts(
    request: Request,
    _principal: Principal = StreamerOnly,
    modality: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    since: str | None = None,
    session_id: Annotated[str | None, Query(max_length=128)] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_ALERTS)] = 50,
    order: Literal["asc", "desc"] = "desc",
) -> dict[str, Any]:
    """``order=desc`` (default) fills a feed with the latest alerts; ``asc`` + ``cursor`` follows new ones."""
    since_ts = None
    if since is not None:
        since_ts = parse_ts(since)
        if since_ts is None:
            raise ApiError(422, "invalid_filter", "since must be an ISO-8601 time")
    events, next_cursor = request.app.state.events.query(
        modalities=_csv(modality, _MODALITY_VALUES, "modality"),
        categories=_csv(category, _CATEGORY_VALUES, "category"),
        min_confidence=min_confidence,
        since=since_ts,
        session_id=session_id,
        cursor=cursor,
        limit=limit,
        newest_first=order == "desc",
    )
    return {"alerts": [alert_view(e) for e in events], "next_cursor": next_cursor, "count": len(events)}
