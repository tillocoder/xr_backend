from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.request_context import get_request_id


LOGGER = logging.getLogger("app.exceptions")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    headers = dict(exc.headers or {})
    headers.setdefault("X-Request-ID", _request_id_from_request(request))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "requestId": _request_id_from_request(request),
        },
        headers=headers,
    )


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed.",
            "requestId": _request_id_from_request(request),
            "errors": exc.errors(),
        },
        headers={"X-Request-ID": _request_id_from_request(request)},
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    settings = get_settings()
    request_id = _request_id_from_request(request)
    LOGGER.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                str(exc)
                if settings.error_include_details_in_response
                else "Internal server error."
            ),
            "requestId": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def _request_id_from_request(request: Request) -> str:
    return getattr(request.state, "request_id", None) or get_request_id()
