# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Consistent English errors, including framework validation failures."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def register_handlers(app: FastAPI) -> None:
    from identity.principal import IdentityError

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(IdentityError)
    async def identity_error(request: Request, exc: IdentityError):
        response = error_response(exc.status, exc.code, exc.message)
        if exc.status == 401:
            response.headers["WWW-Authenticate"] = 'Bearer realm="toolsmith"'
        if getattr(exc, "retry_after", None):
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error_response(400, "INVALID_REQUEST", "Invalid request.")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code >= 500:
            return error_response(exc.status_code, "INTERNAL_ERROR", "Unexpected backend error.")
        return error_response(exc.status_code, "INVALID_REQUEST", "Invalid request.")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("Unexpected HTTP error (%s).", type(exc).__name__)
        response = error_response(500, "INTERNAL_ERROR", "Unexpected backend error.")
        # ServerErrorMiddleware runs outside CORSMiddleware.
        response.headers["Vary"] = "Origin"
        origin = request.headers.get("origin")
        if origin is not None and origin in app.state.settings.cors_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
        return response
