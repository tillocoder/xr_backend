from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import PushToken
from app.services.user_service import ensure_user_exists


class PushTokenService:
    async def register_token(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        token: str,
        platform: str,
    ) -> None:
        normalized_token = token.strip()
        normalized_platform = platform.strip().lower() or "unknown"
        if not user_id.strip() or not normalized_token:
            return
        await ensure_user_exists(db, user_id.strip())

        now = datetime.now(timezone.utc)
        stmt = insert(PushToken).values(
            token=normalized_token,
            user_id=user_id.strip(),
            platform=normalized_platform,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[PushToken.token],
            set_={
                "user_id": user_id.strip(),
                "platform": normalized_platform,
                "updated_at": now,
            },
        )
        await db.execute(stmt)
        await db.commit()

    async def unregister_token(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        token: str,
    ) -> None:
        normalized_token = token.strip()
        if not user_id.strip() or not normalized_token:
            return
        await db.execute(
            delete(PushToken).where(
                PushToken.user_id == user_id.strip(),
                PushToken.token == normalized_token,
            )
        )
        await db.commit()

    async def remove_tokens(self, db: AsyncSession, tokens: list[str]) -> None:
        normalized = [token.strip() for token in tokens if token.strip()]
        if not normalized:
            return
        await db.execute(delete(PushToken).where(PushToken.token.in_(normalized)))
        await db.commit()

    async def list_tokens(self, db: AsyncSession, *, user_id: str) -> list[str]:
        if not user_id.strip():
            return []
        rows = await db.scalars(
            select(PushToken.token)
            .where(PushToken.user_id == user_id.strip())
            .order_by(PushToken.updated_at.desc(), PushToken.token.asc())
        )
        return [token.strip() for token in rows.all() if token.strip()]
