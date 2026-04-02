from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuthSession


async def create_auth_session(
    session: AsyncSession,
    *,
    user_id: str,
    refresh_jti: str,
    expires_at: datetime,
    user_agent: str | None,
    ip_address: str | None,
) -> AuthSession:
    auth_session = AuthSession(
        user_id=user_id,
        refresh_jti=refresh_jti,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.add(auth_session)
    await session.flush()
    await session.refresh(auth_session)
    return auth_session


async def get_active_auth_session(
    session: AsyncSession,
    *,
    refresh_jti: str,
) -> AuthSession | None:
    statement: Select[tuple[AuthSession]] = select(AuthSession).where(
        AuthSession.refresh_jti == refresh_jti,
        AuthSession.revoked_at.is_(None),
    )
    result = await session.execute(statement)
    auth_session = result.scalar_one_or_none()
    if auth_session is None:
        return None
    expires_at = _normalize_utc(auth_session.expires_at)
    if expires_at <= datetime.now(UTC):
        return None
    return auth_session


async def revoke_auth_session(session: AsyncSession, auth_session: AuthSession) -> AuthSession:
    auth_session.revoked_at = datetime.now(UTC)
    session.add(auth_session)
    await session.flush()
    await session.refresh(auth_session)
    return auth_session


async def revoke_all_user_sessions(session: AsyncSession, user_id: str) -> None:
    statement: Select[tuple[AuthSession]] = select(AuthSession).where(
        AuthSession.user_id == user_id,
        AuthSession.revoked_at.is_(None),
    )
    result = await session.execute(statement)
    for auth_session in result.scalars().all():
        auth_session.revoked_at = datetime.now(UTC)
        session.add(auth_session)
    await session.flush()


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
