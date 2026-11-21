"""Safety report endpoints (spec 5.2, 5.9). Streamer and viewer-readonly tokens may read.

* ``GET /api/report/{id}``: the report JSON (partial with ``ended_at: null`` while the stream is live)
* ``GET /api/report/{id}/printable``: the same facts laid out for the web app (EN and HI labels)
* ``GET /api/report/{id}/export?format=json|html``: a download; the HTML has a strict CSP and no script
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..auth import Principal
from ..deps import require_role
from ..errors import ApiError
from ..recorder import SESSION_ID_RE
from ..report import HTML_CSP, render_html, to_printable

router = APIRouter(prefix="/api/report")
Reader = Depends(require_role("streamer", "viewer-readonly"))


def _report(request: Request, session_id: str) -> dict[str, Any]:
    if not SESSION_ID_RE.match(session_id):
        raise ApiError(404, "not_found", "unknown session")
    report = request.app.state.golive.report(session_id)
    if report is None:
        raise ApiError(404, "not_found", "no report for that session (it may have expired)")
    return report


@router.get("")
async def list_reports(request: Request, _: Principal = Reader) -> dict[str, Any]:
    return {"sessions": request.app.state.golive.known_sessions()}


@router.get("/{session_id}")
async def get_report(session_id: str, request: Request, _: Principal = Reader) -> dict[str, Any]:
    return _report(request, session_id)


@router.get("/{session_id}/printable")
async def get_printable(session_id: str, request: Request, _: Principal = Reader) -> dict[str, Any]:
    return to_printable(_report(request, session_id))


@router.get("/{session_id}/export")
async def export_report(
    session_id: str,
    request: Request,
    _: Principal = Reader,
    format: Literal["json", "html"] = Query("json"),  # noqa: A002
) -> Response:
    report = _report(request, session_id)
    name = f"stream-sarthi-report-{session_id}"
    if format == "html":
        return Response(
            render_html(report),
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": HTML_CSP,
                "Content-Disposition": f'attachment; filename="{name}.html"',
            },
        )
    return Response(
        json.dumps(report, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
    )
