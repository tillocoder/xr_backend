from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.system.services import SystemStatusService
from app.bootstrap.container import AppContainer
from app.core.config import Settings
from app.db.session import SessionLocal, get_db
from app.services.auth_session_service import AuthSessionService
from app.services.cache import RedisCache


@dataclass(slots=True)
class CurrentUser:
    id: str


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> CurrentUser:
    token = _bearer_token(authorization)
    if token:
        session_service: AuthSessionService = request.app.state.auth_session_service
        user = await session_service.get_user_for_access_token(db, token)
        if user is None:
            raise HTTPException(status_code=401, detail="Session expired.")
        return CurrentUser(id=user.id)
    if x_user_id and x_user_id.strip():
        return CurrentUser(id=x_user_id.strip())
    raise HTTPException(status_code=401, detail="Authorization header is required.")


async def get_ws_user(ws: WebSocket) -> CurrentUser:
    token = _bearer_token(ws.headers.get("authorization"))
    if not token:
        token = ws.query_params.get("access_token", "").strip()
    if token:
        async with SessionLocal() as db:
            session_service: AuthSessionService = ws.app.state.auth_session_service
            user = await session_service.get_user_for_access_token(db, token)
            if user is not None:
                return CurrentUser(id=user.id)
        await ws.close(code=4401)
        raise HTTPException(status_code=401, detail="Session expired.")
    user_id = ws.query_params.get("user_id", "").strip()
    if user_id:
        return CurrentUser(id=user_id)
    await ws.close(code=4401)
    raise HTTPException(status_code=401, detail="Missing websocket authentication.")


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def get_settings(container: AppContainer = Depends(get_container)) -> Settings:
    return container.settings


def get_cache(container: AppContainer = Depends(get_container)) -> RedisCache:
    return container.cache


def get_bus(container: AppContainer = Depends(get_container)):
    return container.bus


def get_system_status_service(
    container: AppContainer = Depends(get_container),
) -> SystemStatusService:
    return container.system_status_service
