from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import User
from app.services.membership_tiers import MEMBERSHIP_TIER_FREE, MEMBERSHIP_TIER_PRO


async def ensure_user_exists(
    db: AsyncSession,
    user_id: str,
    *,
    display_name: str | None = None,
    avatar_url: str | None = None,
    is_pro: bool | None = None,
) -> User:
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise ValueError("User id is required.")

    existing = await db.get(User, normalized_user_id)
    if existing is not None:
        return existing

    user = User(
        id=normalized_user_id,
        username=_fallback_username(normalized_user_id),
        display_name=(display_name or "XR HODL Member").strip() or "XR HODL Member",
        avatar_url=(avatar_url or "").strip() or None,
        rank_theme=None,
        membership_tier=MEMBERSHIP_TIER_PRO if bool(is_pro) else MEMBERSHIP_TIER_FREE,
        is_pro=bool(is_pro),
        watchlist_json=[],
        holdings_json=[],
        settings_json={},
        linked_wallets_json=[],
    )
    db.add(user)
    await db.flush()
    return user


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    normalized_username = username.strip().lower()
    if not normalized_username:
        return None
    return await db.scalar(select(User).where(User.username == normalized_username))


def _fallback_username(seed: str) -> str:
    compact = "".join(
        char for char in seed.strip().lower() if char.isalnum() or char in {"_", "."}
    )
    compact = compact[:24]
    if len(compact) >= 3:
        return compact
    return f"user_{compact or 'member'}"[:24]
