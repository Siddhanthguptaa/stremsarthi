"""FastAPI dependencies: the calling principal and role checks."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request

from .auth import Principal
from .errors import ApiError


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def current_principal(request: Request) -> Principal:
    state = request.app.state
    token = bearer_token(request)
    principal = state.tokens.verify(token)
    if principal is not None:
        return principal
    if not state.settings.require_auth and token is None:
        # REQUIRE_AUTH=false (local development only): act as the streamer.
        return Principal(role="streamer", room_id=state.tokens.room_id, exp=0, actor="dev")
    raise ApiError(401, "unauthorized", "missing or invalid token")


def require_role(*roles: str) -> Callable[[Principal], Principal]:
    allowed = frozenset(roles)

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:  # noqa: B008
        if principal.role not in allowed:
            raise ApiError(403, "forbidden", "this role may not do that")
        return principal

    return dependency
