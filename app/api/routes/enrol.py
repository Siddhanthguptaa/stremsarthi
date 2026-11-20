"""Face enrolment (streamer only): ``POST|GET /api/enrol`` and ``DELETE /api/enrol/{face_id}``.

Flow (docs/specs/app-api.md 5.5): the browser sends 5 to 12 JPEG frames and a label; a ``FaceEnroller``
(loaded from ``Config.enrol.impl``) returns ``(face_id, embedding)``; the embedding goes into the
in-RAM ``AllowlistStore`` and to the engine as ``AllowlistUpdate(op=add)`` on ``control.allowlist``.
The response is ``EnrolledFaceMeta`` fields only: never the embedding, never an image.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool

from contracts import FaceEnroller
from contracts.privacy import ID_RE

from ..allowlist import AllowlistFull, meta_of
from ..auth import Principal
from ..deps import require_role
from ..enrol_service import (
    AllowlistService,
    check_frames,
    clean_label,
    decode_frames,
    load_enroller,
    parse_json_body,
    parse_multipart,
)
from ..errors import ApiError

log = logging.getLogger("streamsarthi.api.enrol")
router = APIRouter(prefix="/api")

_BUS_ERRORS = (RuntimeError, ValueError, TypeError)


def _meta_dict(face: Any) -> dict[str, Any]:
    m = meta_of(face)
    return {"face_id": m.face_id, "label": m.label, "created_at": m.created_at, "expires_at": m.expires_at}


def _enroller_for(request: Request) -> FaceEnroller:
    state = request.app.state
    injected = getattr(state, "enroller", None)  # tests and embedders inject one
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    impl = state.config_store.config.enrol.impl
    cached = getattr(state, "enroller_cache", None)
    if cached is not None and cached[0] == impl:
        return cached[1]  # type: ignore[no-any-return]
    enroller = load_enroller(impl)
    state.enroller_cache = (impl, enroller)
    return enroller


def _service(request: Request) -> AllowlistService:
    return request.app.state.allowlist  # type: ignore[no-any-return]


@router.post("/enrol", status_code=201)
async def enrol(
    request: Request,
    principal: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, Any]:
    state = request.app.state
    service = _service(request)
    store = service.store
    cfg = state.config_store.config.enrol

    allowed, retry_after = state.enrol_limiter.check(principal.actor)
    if not allowed:
        raise ApiError(429, "rate_limited", f"too many enrolments, retry in {retry_after:.0f} s")
    if state.enrol_inflight >= state.settings.enrol_max_inflight:
        raise ApiError(429, "busy", "another enrolment is running, try again in a moment")
    state.enrol_inflight += 1
    try:
        service.sweep()
        if not store.has_room():
            raise ApiError(422, "allowlist_full", f"at most {store.max_faces} faces can be enrolled")

        body = await request.body()
        content_type = request.headers.get("content-type", "")
        if content_type.lower().startswith("multipart/form-data"):
            raw_label, frames = parse_multipart(content_type, body)
        elif content_type.lower().startswith("application/json"):
            raw_label, frames = parse_json_body(body)
        else:
            raise ApiError(415, "unsupported_media_type", "send multipart/form-data or application/json")
        del body  # frames are separate copies; do not keep the 12 MB request alive
        label = clean_label(raw_label)
        check_frames(frames, cfg.min_frames)
        enroller = _enroller_for(request)

        def work() -> tuple[str, tuple[float, ...]]:
            images = decode_frames(frames)
            try:
                face_id, embedding = enroller.enrol(images, label)
            except ValueError:
                raise ApiError(422, "too_few_faces", "could not find a clear face in enough frames") from None
            except ApiError:
                raise
            except Exception as exc:
                log.warning("enroller failed (%s)", type(exc).__name__)
                raise ApiError(500, "enrol_failed", "face enrolment failed") from None
            finally:
                images.clear()
            return face_id, tuple(float(x) for x in embedding)

        try:
            face_id, embedding = await run_in_threadpool(work)
        finally:
            frames.clear()  # the only copies of the frames in this process

        if not ID_RE.match(face_id) or not embedding or not all(math.isfinite(x) for x in embedding):
            log.warning("enroller returned an invalid result")
            raise ApiError(500, "enrol_failed", "face enrolment failed")
        try:
            face = store.add(face_id, label, embedding)
        except AllowlistFull:
            raise ApiError(
                422, "allowlist_full", f"at most {store.max_faces} faces can be enrolled"
            ) from None
        try:
            service.publish_add(face)
        except _BUS_ERRORS:
            store.remove(face_id)
            log.warning("allowlist publish failed")
            raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
        return _meta_dict(face)
    finally:
        state.enrol_inflight -= 1


@router.get("/enrol")
async def list_enrolled(
    request: Request,
    _: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, Any]:
    service = _service(request)
    service.sweep()
    store = service.store
    return {
        "faces": [_meta_dict(f) for f in store.faces()],
        "max_faces": store.max_faces,
        "ttl_s": store.ttl_s,
        "persisted": store.persistent,
        "min_frames": request.app.state.config_store.config.enrol.min_frames,
        "max_frames": request.app.state.max_enrol_frames,
    }


@router.delete("/enrol/{face_id}")
async def delete_enrolled(
    face_id: str,
    request: Request,
    _: Principal = Depends(require_role("streamer")),  # noqa: B008
) -> dict[str, bool]:
    service = _service(request)
    service.sweep()
    if not ID_RE.match(face_id) or not service.store.remove(face_id):
        raise ApiError(404, "not_found", "no such enrolled face")
    try:
        service.publish_remove(face_id)
    except _BUS_ERRORS:
        log.warning("allowlist publish failed")
        raise ApiError(503, "bus_unavailable", "could not reach the engine bus") from None
    return {"deleted": True}
