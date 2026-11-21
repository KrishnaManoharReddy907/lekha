from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None,
                 details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status
        self.details = details or {}


class NotFound(AppError):
    status = 404
    code = "not_found"


class Forbidden(AppError):
    status = 403
    code = "forbidden"


class Unauthorized(AppError):
    status = 401
    code = "unauthorized"


class Conflict(AppError):
    status = 409
    code = "conflict"


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
                        status_code=exc.status)
