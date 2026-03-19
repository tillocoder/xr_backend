from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.entities import User
from app.services.auth_session_service import AuthSessionService
from app.services.news_feed_service import (
    build_news_feed_payload,
    build_news_list_payload,
    record_news_view,
)
from app.services.text_translation_service import translate_text_via_gemini
from app.services.user_service import ensure_user_exists


router = APIRouter(tags=["compat"])


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


async def _get_cached_payload(request: Request, key: str) -> dict[str, object] | None:
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        return None
    payload = await cache.get_json(key)
    if isinstance(payload, dict):
        return payload
    return None


async def _set_cached_payload(request: Request, key: str, payload: dict[str, object]) -> None:
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        return
    ttl_seconds = max(30, int(getattr(request.app.state.settings, "news_cache_ttl_seconds", 120)))
    await cache.set_json(key, payload, ttl_seconds=ttl_seconds)


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
    session_service: AuthSessionService = request.app.state.auth_session_service
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
    auth_session_service: AuthSessionService = request.app.state.auth_session_service
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
    auth_session_service: AuthSessionService = request.app.state.auth_session_service
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
        auth_session_service: AuthSessionService = request.app.state.auth_session_service
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
        auth_session_service: AuthSessionService = request.app.state.auth_session_service
        await auth_session_service.revoke_access_token(db, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/news/feed")
async def news_feed(
    request: Request,
    limit: int = 40,
    lang: str = "en",
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_limit = max(1, min(limit, 50))
    cache_key = f"news:feed:{lang.lower()}:{normalized_limit}"
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        return cached
    payload = await build_news_feed_payload(db, lang=lang, limit=normalized_limit)
    await _set_cached_payload(request, cache_key, payload)
    return payload


@router.get("/news")
async def news_list(
    request: Request,
    page: int = 1,
    pageSize: int = 20,
    lang: str = "en",
    sort: str = "latest",
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_page = max(1, page)
    normalized_page_size = max(1, min(pageSize, 50))
    normalized_sort = "trending" if (sort or "").strip().lower() == "trending" else "latest"
    normalized_category = (category or "").strip().lower() or "all"
    cache_key = (
        f"news:list:{lang.lower()}:{normalized_page}:{normalized_page_size}:"
        f"{normalized_sort}:{normalized_category}"
    )
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        return cached
    payload = await build_news_list_payload(
        db,
        lang=lang,
        page=normalized_page,
        page_size=normalized_page_size,
        sort=normalized_sort,
        category=None if normalized_category == "all" else normalized_category,
    )
    await _set_cached_payload(request, cache_key, payload)
    return payload


@router.post("/news/read")
async def mark_news_read(
    payload: NewsReadIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    new_count = await record_news_view(
        db,
        article_id=payload.articleId,
        url=payload.url,
    )
    return {"ok": new_count is not None, "viewCount": int(new_count or 0)}


@router.get("/home/overview")
async def home_overview(
    request: Request,
    news_limit: int = 10,
    lang: str = "en",
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    normalized_limit = max(1, min(news_limit, 50))
    cache_key = f"home:overview:{lang.lower()}:{normalized_limit}"
    cached = await _get_cached_payload(request, cache_key)
    if cached is not None:
        return cached
    news = await build_news_feed_payload(db, lang=lang, limit=normalized_limit)
    updated_at = _utc_now().isoformat()
    payload = {"news": news, "updatedAt": updated_at}
    await _set_cached_payload(request, cache_key, payload)
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
