"""Uniform error body: ``{"error": code, "detail": str}``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, detail: str = "", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers


def error_response(
    status: int, code: str, detail: str = "", headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail}, status_code=status, headers=headers)


_STATUS_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    421: "misdirected_request",
    422: "invalid_request",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.detail, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only: never echo the submitted values.
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return error_response(422, "invalid_request", problems[:500])
