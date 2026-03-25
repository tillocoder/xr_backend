from __future__ import annotations

import hashlib
import logging
from time import perf_counter
from uuid import uuid4

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.infrastructure.rate_limit.service import RedisRateLimiter


REQUEST_COUNTER = Counter(
    "xr_backend_http_requests_total",
    "Total HTTP requests handled by XR HODL backend.",
    ("method", "route", "status_code"),
)
REQUEST_LATENCY = Histogram(
    "xr_backend_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._logger = logging.getLogger("app.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        request_id = uuid4().hex
        state["request_id"] = request_id

        started_at = perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration_seconds = perf_counter() - started_at
            route = scope.get("route")
            route_path = getattr(route, "path", scope.get("path", "unknown"))
            method = scope.get("method", "GET")
            REQUEST_COUNTER.labels(method=method, route=route_path, status_code=str(status_code)).inc()
            REQUEST_LATENCY.labels(method=method, route=route_path).observe(duration_seconds)
            self._logger.info(
                "http_request",
                extra={
                    "event": {
                        "requestId": request_id,
                        "method": method,
                        "path": scope.get("path", ""),
                        "route": route_path,
                        "statusCode": status_code,
                        "durationMs": round(duration_seconds * 1000, 2),
                        "clientIp": _client_ip_from_scope(scope),
                    }
                },
            )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._settings.security_headers_enabled:
            await self._app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault(
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=()",
                )
                headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
                if _request_is_https(scope):
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=31536000; includeSubDomains",
                    )
            await send(message)

        await self._app(scope, receive, send_wrapper)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limiter: RedisRateLimiter | None,
    ) -> None:
        self._app = app
        self._settings = settings
        self._limiter = limiter
        self._skipped_prefixes = ("/health", "/ready", "/metrics", "/media")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limiter = self._limiter
        if limiter is None:
            container = getattr(scope.get("app"), "state", None)
            limiter = getattr(getattr(container, "container", None), "rate_limiter", None)

        if scope["type"] != "http" or not self._settings.rate_limit_enabled or limiter is None:
            await self._app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path.startswith(self._skipped_prefixes):
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        ip_key = _client_ip_from_scope(scope)
        user_key = _user_key_from_headers(headers)

        ip_decision = await limiter.check(
            key=f"ratelimit:ip:{ip_key}",
            limit=self._settings.rate_limit_max_requests_per_ip,
        )
        if not ip_decision.allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Too many requests from this IP.", "retryAfter": ip_decision.retry_after_seconds},
            )
            response.headers["Retry-After"] = str(ip_decision.retry_after_seconds)
            await response(scope, receive, send)
            return

        if user_key:
            user_decision = await limiter.check(
                key=f"ratelimit:user:{user_key}",
                limit=self._settings.rate_limit_max_requests_per_user,
            )
            if not user_decision.allowed:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests for this user.", "retryAfter": user_decision.retry_after_seconds},
                )
                response.headers["Retry-After"] = str(user_decision.retry_after_seconds)
                await response(scope, receive, send)
                return

        await self._app(scope, receive, send)


def _client_ip_from_scope(scope: Scope) -> str:
    headers = Headers(scope=scope)
    forwarded_for = headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "unknown"


def _user_key_from_headers(headers: Headers) -> str:
    bearer = headers.get("authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        token = bearer.split(" ", 1)[1].strip()
        if token:
            return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    user_id = headers.get("x-user-id", "").strip()
    if user_id:
        return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    return ""


def _request_is_https(scope: Scope) -> bool:
    headers = Headers(scope=scope)
    forwarded_proto = headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    if forwarded_proto in {"https", "wss"}:
        return True
    cf_visitor = headers.get("cf-visitor", "").lower()
    if '"scheme":"https"' in cf_visitor:
        return True
    return str(scope.get("scheme", "")).lower() == "https"
