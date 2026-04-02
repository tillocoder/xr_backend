from __future__ import annotations

from typing import Any

from fastapi import Request

from app.core.public_url import get_public_base_url_for_request
from app.services.auth_session_service import AuthSessionService
from app.services.cache import RedisCache
from app.services.google_identity_service import GoogleIdentityService


def get_settings_value(
    request: Request,
    name: str,
    default: Any,
) -> Any:
    settings = getattr(request.app.state, "settings", None)
    return getattr(settings, name, default)


def get_optional_cache(request: Request) -> RedisCache | None:
    cache = getattr(request.app.state, "cache", None)
    return cache if isinstance(cache, RedisCache) else None


def get_auth_session_service(request: Request) -> AuthSessionService:
    return request.app.state.auth_session_service


def get_google_identity_service(request: Request) -> GoogleIdentityService:
    return request.app.state.google_identity_service


def get_notification_service(request: Request):
    return request.app.state.notification_service


def get_push_token_service(request: Request):
    return request.app.state.push_token_service


def get_market_runtime_service(request: Request):
    return request.app.state.market_runtime_service


def get_bus(request: Request):
    return request.app.state.bus


def get_ws_manager(request: Request):
    return request.app.state.ws_manager


def get_public_base_url(request: Request) -> str:
    return get_public_base_url_for_request(request)
