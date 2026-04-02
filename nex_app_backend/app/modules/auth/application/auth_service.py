from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    validate_password_strength,
    verify_password,
)
from app.models.entities import User
from app.schemas.auth import AuthResponse, TokenPair
from app.services.auth_session_service import (
    create_auth_session,
    get_active_auth_session,
    revoke_all_user_sessions,
    revoke_auth_session,
)
from app.services.user_service import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    set_password,
)


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str,
    user_agent: str | None,
    ip_address: str | None,
) -> AuthResponse:
    existing_user = await get_user_by_email(session, email)
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists.")
    validate_password_strength(password)

    user = await create_user(
        session,
        email=email,
        full_name=full_name,
        password=password,
    )
    tokens = await _issue_tokens(
        session,
        user=user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await session.commit()
    return AuthResponse(tokens=tokens, user=user)


async def login_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    user_agent: str | None,
    ip_address: str | None,
) -> AuthResponse:
    user = await get_user_by_email(session, email)
    if user is None or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive.",
        )

    tokens = await _issue_tokens(
        session,
        user=user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await session.commit()
    return AuthResponse(tokens=tokens, user=user)


async def refresh_user_tokens(
    session: AsyncSession,
    *,
    refresh_token: str,
    user_agent: str | None,
    ip_address: str | None,
) -> AuthResponse:
    payload = decode_token(refresh_token, expected_type="refresh")
    refresh_jti = str(payload.get("jti", ""))
    auth_session = await get_active_auth_session(session, refresh_jti=refresh_jti)
    if auth_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh session is invalid or expired.",
        )

    user = await get_user_by_id(session, auth_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
        )

    await revoke_auth_session(session, auth_session)
    tokens = await _issue_tokens(
        session,
        user=user,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await session.commit()
    return AuthResponse(tokens=tokens, user=user)


async def logout_user(session: AsyncSession, *, refresh_token: str) -> None:
    payload = decode_token(refresh_token, expected_type="refresh")
    auth_session = await get_active_auth_session(
        session,
        refresh_jti=str(payload.get("jti", "")),
    )
    if auth_session is not None:
        await revoke_auth_session(session, auth_session)
        await session.commit()


async def change_user_password(
    session: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
) -> AuthResponse:
    if not verify_password(current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if current_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password.",
        )
    validate_password_strength(new_password)

    await set_password(session, user, new_password)
    await revoke_all_user_sessions(session, user.id)
    tokens = await _issue_tokens(
        session,
        user=user,
        user_agent=None,
        ip_address=None,
    )
    await session.commit()
    return AuthResponse(tokens=tokens, user=user)


async def _issue_tokens(
    session: AsyncSession,
    *,
    user: User,
    user_agent: str | None,
    ip_address: str | None,
) -> TokenPair:
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
    )
    refresh_token, refresh_jti, refresh_expires_at = create_refresh_token(
        user_id=user.id,
        email=user.email,
    )
    await create_auth_session(
        session,
        user_id=user.id,
        refresh_jti=refresh_jti,
        expires_at=refresh_expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
    )
