from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.entities import User
from app.services.auth_session_service import AuthSessionService
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_uid(email: str | None, id_token: str) -> str:
    seed = (email or id_token).strip().lower()
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


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
async def news_feed(limit: int = 40) -> dict[str, object]:
    normalized_limit = max(1, min(limit, 100))
    return {
        "latest": [],
        "liquidations": [],
        "updatedAt": _utc_now().isoformat(),
        "limit": normalized_limit,
    }


@router.get("/home/overview")
async def home_overview(news_limit: int = 10) -> dict[str, object]:
    normalized_limit = max(1, min(news_limit, 100))
    updated_at = _utc_now().isoformat()
    return {
        "news": {
            "latest": [],
            "liquidations": [],
            "updatedAt": updated_at,
            "limit": normalized_limit,
        },
        "updatedAt": updated_at,
    }


@router.post("/translation/text")
async def translate_text(payload: TranslationIn) -> dict[str, str]:
    # Fallback compatibility response. Flutter already has upstream fallback.
    return {"translatedText": payload.text}
