from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AuthSession, User


@dataclass(slots=True)
class IssuedSession:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


class AuthSessionService:
    def __init__(
        self,
        *,
        access_ttl: timedelta = timedelta(days=30),
        refresh_ttl: timedelta = timedelta(days=180),
        last_seen_touch_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl
        self._last_seen_touch_interval = last_seen_touch_interval

    async def issue_session(self, db: AsyncSession, *, user_id: str) -> IssuedSession:
        now = _utc_now()
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(48)
        session = AuthSession(
            user_id=user_id,
            access_token_hash=_hash_token(access_token),
            refresh_token_hash=_hash_token(refresh_token),
            access_expires_at=now + self._access_ttl,
            refresh_expires_at=now + self._refresh_ttl,
            last_seen_at=now,
        )
        db.add(session)
        await db.commit()
        return IssuedSession(
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=session.access_expires_at,
            refresh_expires_at=session.refresh_expires_at,
        )

    async def get_user_for_access_token(self, db: AsyncSession, token: str) -> User | None:
        session = await self._get_session_by_access_token(db, token)
        if session is None:
            return None
        now = _utc_now()
        if session.refresh_expires_at <= now:
            await self._delete_session(db, session.id)
            return None
        if session.access_expires_at <= now:
            return None
        last_seen_at = session.last_seen_at
        if last_seen_at is None or (now - last_seen_at) >= self._last_seen_touch_interval:
            session.last_seen_at = now
        return await db.get(User, session.user_id)

    async def refresh_session(self, db: AsyncSession, refresh_token: str) -> tuple[User, IssuedSession] | None:
        normalized = refresh_token.strip()
        if not normalized:
            return None
        session = await db.scalar(
            select(AuthSession).where(
                AuthSession.refresh_token_hash == _hash_token(normalized)
            )
        )
        if session is None:
            return None
        now = _utc_now()
        if session.refresh_expires_at <= now:
            await self._delete_session(db, session.id)
            return None

        access_token = secrets.token_urlsafe(32)
        next_refresh_token = secrets.token_urlsafe(48)
        access_expires_at = now + self._access_ttl
        refresh_expires_at = now + self._refresh_ttl
        await db.execute(
            update(AuthSession)
            .where(AuthSession.id == session.id)
            .values(
                access_token_hash=_hash_token(access_token),
                refresh_token_hash=_hash_token(next_refresh_token),
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
                last_seen_at=now,
            )
        )
        await db.commit()
        user = await db.get(User, session.user_id)
        if user is None:
            await self._delete_session(db, session.id)
            return None
        return user, IssuedSession(
            access_token=access_token,
            refresh_token=next_refresh_token,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )

    async def revoke_access_token(self, db: AsyncSession, token: str) -> None:
        normalized = token.strip()
        if not normalized:
            return
        await db.execute(
            delete(AuthSession).where(AuthSession.access_token_hash == _hash_token(normalized))
        )
        await db.commit()

    async def revoke_refresh_token(self, db: AsyncSession, token: str) -> None:
        normalized = token.strip()
        if not normalized:
            return
        await db.execute(
            delete(AuthSession).where(AuthSession.refresh_token_hash == _hash_token(normalized))
        )
        await db.commit()

    async def _get_session_by_access_token(self, db: AsyncSession, token: str) -> AuthSession | None:
        normalized = token.strip()
        if not normalized:
            return None
        return await db.scalar(
            select(AuthSession).where(AuthSession.access_token_hash == _hash_token(normalized))
        )

    async def _delete_session(self, db: AsyncSession, session_id: str) -> None:
        await db.execute(delete(AuthSession).where(AuthSession.id == session_id))
        await db.commit()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
