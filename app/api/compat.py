from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from prometheus_client import Counter, Histogram
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.entities import User
from app.presentation.api.request_state import (
    get_auth_session_service,
    get_optional_cache,
    get_settings_value,
)
from app.services.auth_session_service import AuthSessionService
from app.services.news_query_service import (
    build_news_cache_revision,
    build_news_feed_payload,
    build_news_list_payload,
    build_related_news_payload,
    record_news_view,
)
from app.services.text_translation_service import translate_text_via_gemini
from app.services.user_service import ensure_user_exists


router = APIRouter(tags=["compat"])
_PROCESS_NEWS_PAYLOAD_CACHE: dict[str, tuple[float, str, dict[str, object]]] = {}
_PROCESS_NEWS_REVISION_CACHE: dict[str, tuple[float, str]] = {}
_NEWS_LOGGER = logging.getLogger("app.news")

NEWS_CACHE_EVENTS = Counter(
    "xr_backend_news_cache_events_total",
    "News cache events by endpoint, layer, and outcome.",
    ("endpoint", "layer", "status"),
)
NEWS_REVISION_BUILD_LATENCY = Histogram(
    "xr_backend_news_revision_build_duration_seconds",
    "Time spent computing news revisions from the database.",
    ("endpoint",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
NEWS_PAYLOAD_BUILD_LATENCY = Histogram(
    "xr_backend_news_payload_build_duration_seconds",
    "Time spent building news payloads from the database.",
    ("endpoint",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)


class GoogleAuthIn(BaseModel):
    idToken: str
    email: str | None = None
    displayName: str | None = None
    photoUrl: str | None = None


class TranslationIn(BaseModel):
    text: str
    targetLang: str


class RefreshTokenIn(BaseModel):
    refreshToken: str


class NewsReadIn(BaseModel):
    articleId: int | None = None
    url: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_uid(email: str | None, id_token: str) -> str:
    seed = (email or id_token).strip().lower()
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _make_news_etag(*parts: object) -> str:
    seed = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f'W/"{digest}"'


def _normalize_etag(value: str) -> str:
    normalized = (value or "").strip()
    if normalized.startswith("W/"):
        normalized = normalized[2:].strip()
    return normalized


def _etag_matches(request_etag: str | None, current_etag: str) -> bool:
    if not request_etag:
        return False
    current = _normalize_etag(current_etag)
    for candidate in request_etag.split(","):
        token = candidate.strip()
        if token == "*":
            return True
        if _normalize_etag(token) == current:
            return True
    return False


def _set_news_cache_headers(
    request: Request,
    response: Response,
    *,
    etag: str,
    ttl_seconds: int | None = None,
) -> None:
    effective_ttl = (
        max(30, int(ttl_seconds))
        if ttl_seconds is not None
        else max(30, int(get_settings_value(request, "news_cache_ttl_seconds", 120)))
    )
    max_age = min(45, effective_ttl)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = (
        f"private, max-age={max_age}, stale-while-revalidate={effective_ttl}"
    )
    response.headers["Vary"] = "Accept-Encoding, If-None-Match"


def _not_modified_response(response: Response) -> Response:
    return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=dict(response.headers))


def _process_news_cache_ttl_seconds(request: Request) -> int:
    configured_ttl = max(
        15,
        int(get_settings_value(request, "news_cache_ttl_seconds", 120)),
    )
    return min(120, configured_ttl)


def _related_news_cache_ttl_seconds(request: Request) -> int:
    configured_ttl = max(
        60,
        int(get_settings_value(request, "news_related_cache_ttl_seconds", 300)),
    )
    return min(900, configured_ttl)


def _news_revision_cache_ttl_seconds(request: Request) -> int:
    configured_ttl = max(
        5,
        int(get_settings_value(request, "news_revision_cache_ttl_seconds", 30)),
    )
    return min(60, configured_ttl)


def _get_process_news_payload(
    request: Request,
    key: str,
) -> tuple[str, dict[str, object]] | None:
    cached = _PROCESS_NEWS_PAYLOAD_CACHE.get(key)
    if cached is None:
        return None
    expires_at, etag, payload = cached
    if time.monotonic() >= expires_at:
        _PROCESS_NEWS_PAYLOAD_CACHE.pop(key, None)
        return None
    return etag, payload


def _set_process_news_payload(
    request: Request,
    key: str,
    *,
    etag: str,
    payload: dict[str, object],
    ttl_seconds: int | None = None,
) -> None:
    effective_ttl = (
        max(15, int(ttl_seconds))
        if ttl_seconds is not None
        else _process_news_cache_ttl_seconds(request)
    )
    expires_at = time.monotonic() + effective_ttl
    _PROCESS_NEWS_PAYLOAD_CACHE[key] = (expires_at, etag, payload)


def _delete_process_news_payload_prefix(prefix: str) -> None:
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return
    for key in list(_PROCESS_NEWS_PAYLOAD_CACHE.keys()):
        if key.startswith(normalized_prefix):
            _PROCESS_NEWS_PAYLOAD_CACHE.pop(key, None)


def _get_process_news_revision(key: str) -> str | None:
    cached = _PROCESS_NEWS_REVISION_CACHE.get(key)
    if cached is None:
        return None
    expires_at, revision = cached
    if time.monotonic() >= expires_at:
        _PROCESS_NEWS_REVISION_CACHE.pop(key, None)
        return None
    return revision


def _set_process_news_revision(
    request: Request,
    key: str,
    *,
    revision: str,
) -> None:
    expires_at = time.monotonic() + _news_revision_cache_ttl_seconds(request)
    _PROCESS_NEWS_REVISION_CACHE[key] = (expires_at, revision)


def _delete_process_news_revision_prefix(prefix: str) -> None:
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return
    for key in list(_PROCESS_NEWS_REVISION_CACHE.keys()):
        if key.startswith(normalized_prefix):
            _PROCESS_NEWS_REVISION_CACHE.pop(key, None)


async def _get_cached_payload(request: Request, key: str) -> dict[str, object] | None:
    cache = get_optional_cache(request)
    if cache is None:
        return None
    payload = await cache.get_json(key)
    if isinstance(payload, dict):
        return payload
    return None


async def _set_cached_payload(
    request: Request,
    key: str,
    payload: dict[str, object],
    *,
    ttl_seconds: int | None = None,
) -> None:
    cache = get_optional_cache(request)
    if cache is None:
        return
    effective_ttl = (
        max(30, int(ttl_seconds))
        if ttl_seconds is not None
        else max(30, int(get_settings_value(request, "news_cache_ttl_seconds", 120)))
    )
    await cache.set_json(key, payload, ttl_seconds=effective_ttl)


async def _get_cached_revision(
    request: Request,
    key: str,
) -> str | None:
    cache = get_optional_cache(request)
    if cache is None:
        return None
    payload = await cache.get_json(key)
    if not isinstance(payload, dict):
        return None
    revision = str(payload.get("revision") or "").strip()
    return revision or None


async def _set_cached_revision(
    request: Request,
    key: str,
    *,
    revision: str,
) -> None:
    cache = get_optional_cache(request)
    if cache is None:
        return
    await cache.set_json(
        key,
        {"revision": revision, "updatedAt": _utc_now().isoformat()},
        ttl_seconds=_news_revision_cache_ttl_seconds(request),
    )


def _observe_news_cache_event(
    endpoint: str,
    *,
    layer: str,
    status: str,
) -> None:
    NEWS_CACHE_EVENTS.labels(endpoint=endpoint, layer=layer, status=status).inc()


def _log_news_cache_result(
    request: Request,
    *,
    endpoint: str,
    lang: str,
    result: str,
    revision_source: str,
    payload_source: str | None = None,
    duration_ms: float,
) -> None:
    _NEWS_LOGGER.info(
        "news_cache_result",
        extra={
            "event": {
                "requestId": getattr(request.state, "request_id", ""),
                "endpoint": endpoint,
                "lang": lang,
                "result": result,
                "revisionSource": revision_source,
                "payloadSource": payload_source or "",
                "durationMs": round(duration_ms, 2),
            }
        },
    )


async def _resolve_news_revision(
    request: Request,
    *,
    endpoint: str,
    revision_cache_key: str,
    builder: Callable[[], Awaitable[str]],
) -> tuple[str, Literal["process", "shared", "db"]]:
    process_cached = _get_process_news_revision(revision_cache_key)
    if process_cached is not None:
        _observe_news_cache_event(endpoint, layer="revision_process", status="hit")
        return process_cached, "process"

    shared_cached = await _get_cached_revision(request, revision_cache_key)
    if shared_cached is not None:
        _set_process_news_revision(
            request,
            revision_cache_key,
            revision=shared_cached,
        )
        _observe_news_cache_event(endpoint, layer="revision_shared", status="hit")
        return shared_cached, "shared"

    started_at = perf_counter()
    revision = await builder()
    NEWS_REVISION_BUILD_LATENCY.labels(endpoint=endpoint).observe(
        perf_counter() - started_at
    )
    await _set_cached_revision(
        request,
        revision_cache_key,
        revision=revision,
    )
    _set_process_news_revision(request, revision_cache_key, revision=revision)
    _observe_news_cache_event(endpoint, layer="revision_db", status="build")
    return revision, "db"


async def _invalidate_news_caches(request: Request, *prefixes: str) -> None:
    cache = get_optional_cache(request)
    normalized_prefixes = [prefix.strip() for prefix in prefixes if prefix.strip()]
    if not normalized_prefixes:
        return
    for prefix in normalized_prefixes:
        _delete_process_news_payload_prefix(prefix)
        _delete_process_news_revision_prefix(prefix)
    if cache is None:
        return
    for prefix in normalized_prefixes:
        await cache.delete_json_prefix(prefix)


async def _get_session_payload(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> dict[str, str | None]:
    token = ""
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    session_service: AuthSessionService = get_auth_session_service(request)
    user = await session_service.get_user_for_access_token(db, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired.")
    return {
        "user_id": user.id,
        "email": None,
        "display_name": user.display_name,
        "photo_url": user.avatar_url,
    }


def _user_out(payload: dict[str, str | None]) -> dict[str, str | None]:
    return {
        "id": payload["user_id"],
        "email": payload["email"],
        "displayName": payload["display_name"],
        "photoUrl": payload["photo_url"],
        "authProvider": "google.com",
    }


def _news_item_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("url") or "").strip(),
        str(item.get("publishedAt") or "").strip(),
        str(item.get("title") or "").strip(),
    )


def _merge_news_items(
    *groups: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for item in group:
            key = _news_item_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def _merge_news_payloads(
    stored_payload: dict[str, object],
    runtime_payload: dict[str, object] | None,
    *,
    limit: int,
) -> dict[str, object]:
    if runtime_payload is None:
        return stored_payload

    runtime_latest = list(runtime_payload.get("latest") or [])
    runtime_liquidations = list(runtime_payload.get("liquidations") or [])
    stored_latest = list(stored_payload.get("latest") or [])
    stored_liquidations = list(stored_payload.get("liquidations") or [])

    return {
        "latest": _merge_news_items(runtime_latest, stored_latest, limit=limit),
        "liquidations": _merge_news_items(
            runtime_liquidations,
            stored_liquidations,
            limit=limit,
        ),
        "updatedAt": str(
            runtime_payload.get("updatedAt")
            or stored_payload.get("updatedAt")
            or _utc_now().isoformat()
        ),
        "lang": str(runtime_payload.get("lang") or stored_payload.get("lang") or "en"),
        "aiEnabled": bool(
            runtime_payload.get("aiEnabled") or stored_payload.get("aiEnabled")
        ),
        "limit": max(1, min(int(limit or 40), 50)),
    }


@router.get("/health")
async def api_health() -> dict[str, bool]:
    return {"ok": True}


@router.post("/auth/google")
async def auth_google(
    payload: GoogleAuthIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    user_id = _make_uid(payload.email, payload.idToken)
    display_name = (payload.displayName or "").strip() or "XR HODL Member"
    photo_url = (payload.photoUrl or "").strip() or None

    user = await ensure_user_exists(
        db,
        user_id,
        display_name=display_name,
        avatar_url=photo_url,
    )
    changed = False
    if display_name and user.display_name != display_name:
        user.display_name = display_name
        changed = True
    if photo_url and user.avatar_url != photo_url:
        user.avatar_url = photo_url
        changed = True
    if changed:
        await db.commit()

    session_payload = {
        "user_id": user_id,
        "email": (payload.email or "").strip() or None,
        "display_name": display_name,
        "photo_url": photo_url,
    }
    auth_session_service: AuthSessionService = get_auth_session_service(request)
    session = await auth_session_service.issue_session(db, user_id=user_id)
    return {
        "accessToken": session.access_token,
        "refreshToken": session.refresh_token,
        "accessExpiresAt": session.access_expires_at.isoformat(),
        "refreshExpiresAt": session.refresh_expires_at.isoformat(),
        "expiresAt": session.access_expires_at.isoformat(),
        "user": _user_out(session_payload),
    }


@router.get("/auth/session")
async def auth_session(
    payload: dict[str, str | None] = Depends(_get_session_payload),
) -> dict[str, str | None]:
    return _user_out(payload)


@router.post("/auth/refresh")
async def auth_refresh(
    payload: RefreshTokenIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    auth_session_service: AuthSessionService = get_auth_session_service(request)
    refreshed = await auth_session_service.refresh_session(db, payload.refreshToken)
    if refreshed is None:
        raise HTTPException(status_code=401, detail="Refresh token expired.")
    user, session = refreshed
    user_payload = {
        "user_id": user.id,
        "email": None,
        "display_name": user.display_name,
        "photo_url": user.avatar_url,
    }
    return {
        "accessToken": session.access_token,
        "refreshToken": session.refresh_token,
        "accessExpiresAt": session.access_expires_at.isoformat(),
        "refreshExpiresAt": session.refresh_expires_at.isoformat(),
        "expiresAt": session.access_expires_at.isoformat(),
        "user": _user_out(user_payload),
    }


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def auth_logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> Response:
    token = ""
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if token:
        auth_session_service: AuthSessionService = get_auth_session_service(request)
        await auth_session_service.revoke_access_token(db, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/auth/account", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_account(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> Response:
    token = ""
    if authorization:
        parts = authorization.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if token:
        auth_session_service: AuthSessionService = get_auth_session_service(request)
        await auth_session_service.revoke_access_token(db, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/news/feed")
async def news_feed(
    request: Request,
    response: Response,
    limit: int = 18,
    lang: str = "en",
    if_none_match: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_limit = max(1, min(limit, 20))
    normalized_lang = lang.lower()
    endpoint = "news_feed"
    started_at = perf_counter()
    process_cache_key = f"news:feed:v4:{normalized_lang}:{normalized_limit}"
    revision_cache_key = f"news:revision:v1:feed:{normalized_lang}:all:latest"
    revision, revision_source = await _resolve_news_revision(
        request,
        endpoint=endpoint,
        revision_cache_key=revision_cache_key,
        builder=lambda: build_news_cache_revision(
            db,
            include_views=False,
            lang=lang,
        ),
    )
    etag = _make_news_etag("news-feed-v3", normalized_lang, normalized_limit, revision)
    _set_news_cache_headers(request, response, etag=etag)
    process_cached = _get_process_news_payload(request, process_cache_key)
    if process_cached is not None:
        cached_etag, payload = process_cached
        if cached_etag == etag:
            _observe_news_cache_event(endpoint, layer="payload_process", status="hit")
            if _etag_matches(if_none_match, etag):
                _observe_news_cache_event(endpoint, layer="response", status="not_modified")
                _log_news_cache_result(
                    request,
                    endpoint=endpoint,
                    lang=normalized_lang,
                    result="not_modified",
                    revision_source=revision_source,
                    payload_source="process",
                    duration_ms=(perf_counter() - started_at) * 1000,
                )
                return _not_modified_response(response)
            _log_news_cache_result(
                request,
                endpoint=endpoint,
                lang=normalized_lang,
                result="ok",
                revision_source=revision_source,
                payload_source="process",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return payload
        _observe_news_cache_event(endpoint, layer="payload_process", status="stale")
    if _etag_matches(if_none_match, etag):
        _observe_news_cache_event(endpoint, layer="response", status="not_modified")
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="not_modified",
            revision_source=revision_source,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return _not_modified_response(response)
    cache_key = f"news:feed:v3:{normalized_lang}:{normalized_limit}:{revision}"
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        _observe_news_cache_event(endpoint, layer="payload_shared", status="hit")
        _set_process_news_payload(request, process_cache_key, etag=etag, payload=cached)
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="ok",
            revision_source=revision_source,
            payload_source="shared",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return cached
    build_started_at = perf_counter()
    payload = await build_news_feed_payload(db, lang=lang, limit=normalized_limit)
    NEWS_PAYLOAD_BUILD_LATENCY.labels(endpoint=endpoint).observe(
        perf_counter() - build_started_at
    )
    _observe_news_cache_event(endpoint, layer="payload_db", status="build")
    await _set_cached_payload(request, cache_key, payload)
    _set_process_news_payload(request, process_cache_key, etag=etag, payload=payload)
    _log_news_cache_result(
        request,
        endpoint=endpoint,
        lang=normalized_lang,
        result="ok",
        revision_source=revision_source,
        payload_source="db",
        duration_ms=(perf_counter() - started_at) * 1000,
    )
    return payload


@router.get("/news")
async def news_list(
    request: Request,
    response: Response,
    page: int = 1,
    pageSize: int = 30,
    lang: str = "en",
    sort: str = "latest",
    category: str | None = None,
    if_none_match: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_page = max(1, page)
    normalized_page_size = max(1, min(pageSize, 30))
    normalized_sort = "trending" if (sort or "").strip().lower() == "trending" else "latest"
    normalized_category = (category or "").strip().lower() or "all"
    normalized_lang = lang.lower()
    endpoint = "news_list"
    started_at = perf_counter()
    process_cache_key = (
        f"news:list:v4:{normalized_lang}:{normalized_page}:{normalized_page_size}:"
        f"{normalized_sort}:{normalized_category}"
    )
    revision_cache_key = (
        f"news:revision:v1:list:{normalized_lang}:{normalized_sort}:{normalized_category}:"
        "liquidation-false"
    )
    revision, revision_source = await _resolve_news_revision(
        request,
        endpoint=endpoint,
        revision_cache_key=revision_cache_key,
        builder=lambda: build_news_cache_revision(
            db,
            is_liquidation=False,
            category=None if normalized_category == "all" else normalized_category,
            include_views=normalized_sort == "trending",
            lang=lang,
        ),
    )
    etag = _make_news_etag(
        "news-list-v3",
        normalized_lang,
        normalized_page,
        normalized_page_size,
        normalized_sort,
        normalized_category,
        revision,
    )
    _set_news_cache_headers(request, response, etag=etag)
    process_cached = _get_process_news_payload(request, process_cache_key)
    if process_cached is not None:
        cached_etag, payload = process_cached
        if cached_etag == etag:
            _observe_news_cache_event(endpoint, layer="payload_process", status="hit")
            if _etag_matches(if_none_match, etag):
                _observe_news_cache_event(endpoint, layer="response", status="not_modified")
                _log_news_cache_result(
                    request,
                    endpoint=endpoint,
                    lang=normalized_lang,
                    result="not_modified",
                    revision_source=revision_source,
                    payload_source="process",
                    duration_ms=(perf_counter() - started_at) * 1000,
                )
                return _not_modified_response(response)
            _log_news_cache_result(
                request,
                endpoint=endpoint,
                lang=normalized_lang,
                result="ok",
                revision_source=revision_source,
                payload_source="process",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return payload
        _observe_news_cache_event(endpoint, layer="payload_process", status="stale")
    if _etag_matches(if_none_match, etag):
        _observe_news_cache_event(endpoint, layer="response", status="not_modified")
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="not_modified",
            revision_source=revision_source,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return _not_modified_response(response)
    cache_key = (
        f"news:list:v3:{normalized_lang}:{normalized_page}:{normalized_page_size}:"
        f"{normalized_sort}:{normalized_category}:{revision}"
    )
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        _observe_news_cache_event(endpoint, layer="payload_shared", status="hit")
        _set_process_news_payload(request, process_cache_key, etag=etag, payload=cached)
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="ok",
            revision_source=revision_source,
            payload_source="shared",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return cached
    build_started_at = perf_counter()
    payload = await build_news_list_payload(
        db,
        lang=lang,
        page=normalized_page,
        page_size=normalized_page_size,
        sort=normalized_sort,
        category=None if normalized_category == "all" else normalized_category,
    )
    NEWS_PAYLOAD_BUILD_LATENCY.labels(endpoint=endpoint).observe(
        perf_counter() - build_started_at
    )
    _observe_news_cache_event(endpoint, layer="payload_db", status="build")
    await _set_cached_payload(request, cache_key, payload)
    _set_process_news_payload(request, process_cache_key, etag=etag, payload=payload)
    _log_news_cache_result(
        request,
        endpoint=endpoint,
        lang=normalized_lang,
        result="ok",
        revision_source=revision_source,
        payload_source="db",
        duration_ms=(perf_counter() - started_at) * 1000,
    )
    return payload


@router.get("/news/related")
async def related_news(
    request: Request,
    response: Response,
    url: str,
    limit: int = 24,
    lang: str = "en",
    if_none_match: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_limit = max(1, min(limit, 36))
    normalized_lang = lang.lower()
    normalized_url = str(url or "").strip()
    endpoint = "news_related"
    started_at = perf_counter()
    related_cache_ttl_seconds = _related_news_cache_ttl_seconds(request)
    url_digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:20]
    process_cache_key = (
        f"news:related:v1:{normalized_lang}:{normalized_limit}:{url_digest}"
    )
    revision_cache_key = f"news:revision:v1:feed:{normalized_lang}:all:latest"
    revision, revision_source = await _resolve_news_revision(
        request,
        endpoint=endpoint,
        revision_cache_key=revision_cache_key,
        builder=lambda: build_news_cache_revision(
            db,
            include_views=False,
            lang=lang,
        ),
    )
    etag = _make_news_etag(
        "news-related-v1",
        normalized_lang,
        normalized_limit,
        url_digest,
        revision,
    )
    _set_news_cache_headers(
        request,
        response,
        etag=etag,
        ttl_seconds=related_cache_ttl_seconds,
    )
    process_cached = _get_process_news_payload(request, process_cache_key)
    if process_cached is not None:
        cached_etag, payload = process_cached
        if cached_etag == etag:
            _observe_news_cache_event(endpoint, layer="payload_process", status="hit")
            if _etag_matches(if_none_match, etag):
                _observe_news_cache_event(endpoint, layer="response", status="not_modified")
                _log_news_cache_result(
                    request,
                    endpoint=endpoint,
                    lang=normalized_lang,
                    result="not_modified",
                    revision_source=revision_source,
                    payload_source="process",
                    duration_ms=(perf_counter() - started_at) * 1000,
                )
                return _not_modified_response(response)
            _log_news_cache_result(
                request,
                endpoint=endpoint,
                lang=normalized_lang,
                result="ok",
                revision_source=revision_source,
                payload_source="process",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return payload
        _observe_news_cache_event(endpoint, layer="payload_process", status="stale")
    if _etag_matches(if_none_match, etag):
        _observe_news_cache_event(endpoint, layer="response", status="not_modified")
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="not_modified",
            revision_source=revision_source,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return _not_modified_response(response)
    cache_key = f"news:related:v1:{normalized_lang}:{normalized_limit}:{url_digest}:{revision}"
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        _observe_news_cache_event(endpoint, layer="payload_shared", status="hit")
        _set_process_news_payload(
            request,
            process_cache_key,
            etag=etag,
            payload=cached,
            ttl_seconds=related_cache_ttl_seconds,
        )
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="ok",
            revision_source=revision_source,
            payload_source="shared",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return cached
    build_started_at = perf_counter()
    payload = await build_related_news_payload(
        db,
        lang=lang,
        url=normalized_url,
        limit=normalized_limit,
    )
    NEWS_PAYLOAD_BUILD_LATENCY.labels(endpoint=endpoint).observe(
        perf_counter() - build_started_at
    )
    _observe_news_cache_event(endpoint, layer="payload_db", status="build")
    await _set_cached_payload(
        request,
        cache_key,
        payload,
        ttl_seconds=related_cache_ttl_seconds,
    )
    _set_process_news_payload(
        request,
        process_cache_key,
        etag=etag,
        payload=payload,
        ttl_seconds=related_cache_ttl_seconds,
    )
    _log_news_cache_result(
        request,
        endpoint=endpoint,
        lang=normalized_lang,
        result="ok",
        revision_source=revision_source,
        payload_source="db",
        duration_ms=(perf_counter() - started_at) * 1000,
    )
    return payload


@router.post("/news/read")
async def mark_news_read(
    request: Request,
    payload: NewsReadIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    new_count = await record_news_view(
        db,
        article_id=payload.articleId,
        url=payload.url,
    )
    if new_count is not None:
        await _invalidate_news_caches(
            request,
            "news:list:",
            "news:related:",
            "news:revision:",
        )
    return {"ok": new_count is not None, "viewCount": int(new_count or 0)}


@router.get("/home/overview")
async def home_overview(
    request: Request,
    response: Response,
    news_limit: int = 18,
    lang: str = "en",
    if_none_match: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_limit = max(1, min(news_limit, 18))
    normalized_lang = lang.lower()
    endpoint = "home_overview"
    started_at = perf_counter()
    process_cache_key = f"home:overview:v4:{normalized_lang}:{normalized_limit}"
    revision_cache_key = f"news:revision:v1:feed:{normalized_lang}:all:latest"
    feed_revision, revision_source = await _resolve_news_revision(
        request,
        endpoint=endpoint,
        revision_cache_key=revision_cache_key,
        builder=lambda: build_news_cache_revision(
            db,
            include_views=False,
            lang=lang,
        ),
    )
    etag = _make_news_etag("home-overview-v3", normalized_lang, normalized_limit, feed_revision)
    _set_news_cache_headers(request, response, etag=etag)
    process_cached = _get_process_news_payload(request, process_cache_key)
    if process_cached is not None:
        cached_etag, payload = process_cached
        if cached_etag == etag:
            _observe_news_cache_event(endpoint, layer="payload_process", status="hit")
            if _etag_matches(if_none_match, etag):
                _observe_news_cache_event(endpoint, layer="response", status="not_modified")
                _log_news_cache_result(
                    request,
                    endpoint=endpoint,
                    lang=normalized_lang,
                    result="not_modified",
                    revision_source=revision_source,
                    payload_source="process",
                    duration_ms=(perf_counter() - started_at) * 1000,
                )
                return _not_modified_response(response)
            _log_news_cache_result(
                request,
                endpoint=endpoint,
                lang=normalized_lang,
                result="ok",
                revision_source=revision_source,
                payload_source="process",
                duration_ms=(perf_counter() - started_at) * 1000,
            )
            return payload
        _observe_news_cache_event(endpoint, layer="payload_process", status="stale")
    if _etag_matches(if_none_match, etag):
        _observe_news_cache_event(endpoint, layer="response", status="not_modified")
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="not_modified",
            revision_source=revision_source,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return _not_modified_response(response)
    cache_key = f"home:overview:v3:{normalized_lang}:{normalized_limit}:{feed_revision}"
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        _observe_news_cache_event(endpoint, layer="payload_shared", status="hit")
        _set_process_news_payload(request, process_cache_key, etag=etag, payload=cached)
        _log_news_cache_result(
            request,
            endpoint=endpoint,
            lang=normalized_lang,
            result="ok",
            revision_source=revision_source,
            payload_source="shared",
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return cached
    feed_cache_key = f"news:feed:v3:{normalized_lang}:{normalized_limit}:{feed_revision}"
    news = await _get_cached_payload(request, feed_cache_key)
    payload_source = "shared"
    if news is None:
        build_started_at = perf_counter()
        news = await build_news_feed_payload(db, lang=lang, limit=normalized_limit)
        NEWS_PAYLOAD_BUILD_LATENCY.labels(endpoint="home_overview_feed").observe(
            perf_counter() - build_started_at
        )
        await _set_cached_payload(request, feed_cache_key, news)
        _observe_news_cache_event(endpoint, layer="news_feed_db", status="build")
        payload_source = "db"
    else:
        _observe_news_cache_event(endpoint, layer="news_feed_shared", status="hit")
    payload = {
        "news": news,
        "updatedAt": str(news.get("updatedAt") or _utc_now().isoformat()),
    }
    await _set_cached_payload(request, cache_key, payload)
    _set_process_news_payload(request, process_cache_key, etag=etag, payload=payload)
    _log_news_cache_result(
        request,
        endpoint=endpoint,
        lang=normalized_lang,
        result="ok",
        revision_source=revision_source,
        payload_source=payload_source,
        duration_ms=(perf_counter() - started_at) * 1000,
    )
    return payload


@router.post("/translation/text")
async def translate_text(
    payload: TranslationIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    translated = await translate_text_via_gemini(
        db,
        text=payload.text,
        target_lang=payload.targetLang,
    )
    if translated is None:
        return {"translatedText": payload.text}
    text, _model = translated
    return {"translatedText": text}
