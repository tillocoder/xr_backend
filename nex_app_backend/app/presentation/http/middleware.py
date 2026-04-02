from __future__ import annotations

import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import reset_request_id, set_request_id


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._logger = logging.getLogger("app.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = uuid4().hex
        incoming_request_id = Headers(scope=scope).get("x-request-id", "").strip()
        if incoming_request_id:
            request_id = incoming_request_id[:64]
        started_at = perf_counter()
        status_code = 500
        request_context_token = set_request_id(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
                headers.append(
                    "X-Process-Time-Ms",
                    str(round((perf_counter() - started_at) * 1000, 2)),
                )
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            self._logger.info(
                "http_request method=%s path=%s status=%s duration_ms=%s ip=%s user_agent=%s",
                scope.get("method", "GET"),
                scope.get("path", ""),
                status_code,
                duration_ms,
                _client_ip_from_scope(scope),
                Headers(scope=scope).get("user-agent", ""),
            )
            reset_request_id(request_context_token)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
            await send(message)

        await self._app(scope, receive, send_wrapper)


def _client_ip_from_scope(scope: Scope) -> str:
    headers = Headers(scope=scope)
    forwarded_for = headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "unknown"
