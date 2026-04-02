from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.entities import User
from app.modules.auth.application.auth_service import (
    change_user_password,
    login_user,
    logout_user,
    refresh_user_tokens,
    register_user,
)
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    LogoutResponse,
    RegisterRequest,
    TokenRefreshRequest,
    UserRead,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def _request_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _request_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


@router.post("/register", response_model=AuthResponse)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AuthResponse:
    return await register_user(
        session,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        user_agent=_request_user_agent(request),
        ip_address=_request_ip(request),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AuthResponse:
    return await login_user(
        session,
        email=payload.email,
        password=payload.password,
        user_agent=_request_user_agent(request),
        ip_address=_request_ip(request),
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    payload: TokenRefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AuthResponse:
    return await refresh_user_tokens(
        session,
        refresh_token=payload.refresh_token,
        user_agent=_request_user_agent(request),
        ip_address=_request_ip(request),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    payload: TokenRefreshRequest,
    session: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    await logout_user(session, refresh_token=payload.refresh_token)
    return LogoutResponse(detail="Logged out successfully.")


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@router.post("/change-password", response_model=AuthResponse)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> AuthResponse:
    return await change_user_password(
        session,
        user=current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )

