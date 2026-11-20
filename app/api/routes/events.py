"""``GET /api/events`` and ``GET /api/events/summary``: the SafetyEvent ring (category, time, confidence).

Streamer: every modality. Moderator: chat and system events only (same rule as the WebSocket ACL).
Events carry no text and no images. Events come back as wire envelopes (``{"t","v","d"}``).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from contracts import Category, Modality, to_wire

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError
from ..events import MAX_PAGE, parse_ts

router = APIRouter(prefix="/api")

MOD_MODALITIES = frozenset({Modality.CHAT.value, Modality.SYSTEM.value})
_MODALITY_VALUES = frozenset(m.value for m in Modality)
_CATEGORY_VALUES = frozenset(c.value for c in Category)

ReadRole = Depends(require_role("streamer", "moderator"))


def _csv(values: list[str] | None, allowed: frozenset[str], name: str) -> frozenset[str] | None:
    if not values:
        return None
    out = {part.strip() for value in values for part in value.split(",") if part.strip()}
    unknown = out - allowed
    if unknown:
        raise ApiError(422, "invalid_filter", f"unknown {name}")
    return frozenset(out)


def _time(value: str | None, name: str) -> float | None:
    if value is None:
        return None
    ts = parse_ts(value)
    if ts is None:
        raise ApiError(422, "invalid_filter", f"{name} must be an ISO-8601 time")
    return ts


def _filters(
    principal: Principal,
    modality: list[str] | None,
    category: list[str] | None,
    since: str | None,
    until: str | None,
) -> dict[str, Any]:
    modalities = _csv(modality, _MODALITY_VALUES, "modality")
    if principal.role == "moderator":
        if modalities is not None and not modalities <= MOD_MODALITIES:
            raise ApiError(403, "forbidden", "moderators may read chat and system events only")
        modalities = modalities or MOD_MODALITIES
    return {
        "modalities": modalities,
        "categories": _csv(category, _CATEGORY_VALUES, "category"),
        "since": _time(since, "since"),
        "until": _time(until, "until"),
    }


@router.get("/events")
async def list_events(
    request: Request,
    principal: Principal = ReadRole,
    modality: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    since: str | None = None,
    until: str | None = None,
    session_id: Annotated[str | None, Query(max_length=128)] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 200,
    order: Literal["asc", "desc"] = "asc",
) -> dict[str, Any]:
    store = request.app.state.events
    events, next_cursor = store.query(
        **_filters(principal, modality, category, since, until),
        session_id=session_id,
        cursor=cursor,
        limit=limit,
        newest_first=order == "desc",
    )
    return {"events": [to_wire(e) for e in events], "next_cursor": next_cursor, "count": len(events)}


@router.get("/events/summary")
async def events_summary(
    request: Request,
    principal: Principal = ReadRole,
    modality: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    since: str | None = None,
    until: str | None = None,
    session_id: Annotated[str | None, Query(max_length=128)] = None,
) -> dict[str, Any]:
    store = request.app.state.events
    return store.summary(**_filters(principal, modality, category, since, until), session_id=session_id)  # type: ignore[no-any-return]
