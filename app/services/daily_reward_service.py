from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Post, User
from app.services.membership_tiers import (
    MEMBERSHIP_TIER_FREE,
    MEMBERSHIP_TIER_LEGEND,
    MEMBERSHIP_TIER_PRO,
    is_paid_membership_tier,
    normalize_membership_tier,
)
from app.services.user_service import ensure_user_exists


_REWARD_TZ = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class DailyRewardStatus:
    diamonds_balance: int
    current_streak: int
    streak_goal: int
    reward_per_claim: int
    reward_pro_cost: int
    reward_pro_duration_days: int
    reward_pro_posts_per_day: int
    paid_pro_posts_per_day: int
    can_claim_now: bool
    last_claimed_at: datetime | None
    next_claim_at: datetime | None
    reward_pro_expires_at: datetime | None
    membership_tier: str
    effective_membership_tier: str
    paid_pro_active: bool
    reward_pro_active: bool
    effective_pro_active: bool
    reward_pro_remaining_seconds: int


class DailyRewardService:
    reward_per_claim = 100
    streak_goal = 7
    reward_pro_cost = 700
    reward_pro_duration_days = 5
    reward_pro_posts_per_day = 5
    paid_pro_posts_per_day = 20

    async def get_status(self, db: AsyncSession, *, user_id: str) -> DailyRewardStatus:
        _user, status = await self.get_status_with_user(db, user_id=user_id)
        return status

    async def get_status_with_user(
        self,
        db: AsyncSession,
        *,
        user_id: str,
    ) -> tuple[User, DailyRewardStatus]:
        user, created = await self._ensure_user(db, user_id)
        changed = await self._maybe_activate_reward_pro(db, user)
        if created or changed:
            await db.commit()
        return user, self._status_from_user(user)

    async def claim(self, db: AsyncSession, *, user_id: str) -> DailyRewardStatus:
        user, _ = await self._ensure_user(db, user_id)
        now = self._now()
        today = self._reward_day(now)
        last_claimed_at = self._normalize_datetime(user.daily_reward_last_claimed_at)
        last_claimed_day = self._reward_day(last_claimed_at) if last_claimed_at is not None else None
        if last_claimed_day == today:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Today's daily reward has already been claimed.",
            )

        if last_claimed_day is not None and today == last_claimed_day + timedelta(days=1):
            next_streak = min(self._non_negative_int(user.daily_reward_streak) + 1, self.streak_goal)
        else:
            next_streak = 1

        user.daily_reward_streak = next_streak
        user.daily_reward_last_claimed_at = now
        user.diamonds_balance = self._non_negative_int(user.diamonds_balance) + self.reward_per_claim
        await self._maybe_activate_reward_pro(db, user, now=now)
        await db.commit()
        return self._status_from_user(user, now=now)

    async def get_or_create_user(self, db: AsyncSession, *, user_id: str) -> User:
        user, created = await self._ensure_user(db, user_id)
        changed = await self._maybe_activate_reward_pro(db, user)
        if created or changed:
            await db.commit()
        return user

    async def is_effective_pro(self, db: AsyncSession, *, user_id: str) -> bool:
        user, created = await self._ensure_user(db, user_id)
        changed = await self._maybe_activate_reward_pro(db, user)
        if created or changed:
            await db.commit()
        return self.is_effective_pro_user(user)

    def is_effective_pro_user(self, user: User, *, now: datetime | None = None) -> bool:
        return self.effective_membership_tier_user(user, now=now) != MEMBERSHIP_TIER_FREE

    def is_reward_limited_pro_user(self, user: User, *, now: datetime | None = None) -> bool:
        return (
            self.paid_membership_tier_user(user) == MEMBERSHIP_TIER_FREE
            and self.is_reward_pro_active(user, now=now)
        )

    def paid_membership_tier_user(self, user: User) -> str:
        paid_tier = normalize_membership_tier(getattr(user, "membership_tier", None))
        if paid_tier != MEMBERSHIP_TIER_FREE:
            return paid_tier
        if bool(user.is_pro):
            return MEMBERSHIP_TIER_PRO
        return MEMBERSHIP_TIER_FREE

    def effective_membership_tier_user(
        self,
        user: User,
        *,
        now: datetime | None = None,
    ) -> str:
        paid_tier = self.paid_membership_tier_user(user)
        if paid_tier == MEMBERSHIP_TIER_LEGEND:
            return MEMBERSHIP_TIER_LEGEND
        if paid_tier == MEMBERSHIP_TIER_PRO:
            return MEMBERSHIP_TIER_PRO
        if self.is_reward_pro_active(user, now=now):
            return MEMBERSHIP_TIER_PRO
        return MEMBERSHIP_TIER_FREE

    def is_paid_membership_active_user(self, user: User) -> bool:
        return is_paid_membership_tier(self.paid_membership_tier_user(user))

    def is_reward_pro_active(self, user: User, *, now: datetime | None = None) -> bool:
        expires_at = self._normalize_datetime(user.reward_pro_expires_at)
        if expires_at is None:
            return False
        current_time = now or self._now()
        return expires_at > current_time

    async def count_posts_for_reward_day(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        now: datetime | None = None,
    ) -> int:
        current_time = now or self._now()
        start_at, end_at = self.reward_day_window(current_time)
        count = await db.scalar(
            select(func.count(Post.id)).where(
                Post.author_id == user_id.strip(),
                Post.created_at >= start_at,
                Post.created_at < end_at,
            )
        )
        return self._non_negative_int(count)

    def reward_day_window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        current_time = now or self._now()
        local_now = current_time.astimezone(_REWARD_TZ)
        local_start = datetime.combine(local_now.date(), time.min, tzinfo=_REWARD_TZ)
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)

    async def _maybe_activate_reward_pro(
        self,
        db: AsyncSession,
        user: User,
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or self._now()
        if self.is_paid_membership_active_user(user) or self.is_reward_pro_active(
            user,
            now=current_time,
        ):
            return False
        balance = self._non_negative_int(user.diamonds_balance)
        if balance < self.reward_pro_cost:
            return False
        user.diamonds_balance = balance - self.reward_pro_cost
        user.reward_pro_expires_at = current_time + timedelta(days=self.reward_pro_duration_days)
        await db.flush()
        return True

    def _status_from_user(self, user: User, *, now: datetime | None = None) -> DailyRewardStatus:
        current_time = now or self._now()
        last_claimed_at = self._normalize_datetime(user.daily_reward_last_claimed_at)
        reward_pro_expires_at = self._normalize_datetime(user.reward_pro_expires_at)
        today = self._reward_day(current_time)
        last_claimed_day = self._reward_day(last_claimed_at) if last_claimed_at is not None else None
        can_claim_now = last_claimed_day != today
        next_claim_at = None if can_claim_now else self._next_claim_at(today)
        reward_pro_active = self.is_reward_pro_active(user, now=current_time)
        membership_tier = self.paid_membership_tier_user(user)
        effective_membership_tier = self.effective_membership_tier_user(
            user,
            now=current_time,
        )
        remaining_seconds = 0
        if reward_pro_active and reward_pro_expires_at is not None:
            remaining_seconds = max(
                0,
                int((reward_pro_expires_at - current_time).total_seconds()),
            )
        return DailyRewardStatus(
            diamonds_balance=self._non_negative_int(user.diamonds_balance),
            current_streak=min(self._non_negative_int(user.daily_reward_streak), self.streak_goal),
            streak_goal=self.streak_goal,
            reward_per_claim=self.reward_per_claim,
            reward_pro_cost=self.reward_pro_cost,
            reward_pro_duration_days=self.reward_pro_duration_days,
            reward_pro_posts_per_day=self.reward_pro_posts_per_day,
            paid_pro_posts_per_day=self.paid_pro_posts_per_day,
            can_claim_now=can_claim_now,
            last_claimed_at=last_claimed_at,
            next_claim_at=next_claim_at,
            reward_pro_expires_at=reward_pro_expires_at if reward_pro_active else None,
            membership_tier=membership_tier,
            effective_membership_tier=effective_membership_tier,
            paid_pro_active=self.is_paid_membership_active_user(user),
            reward_pro_active=reward_pro_active,
            effective_pro_active=effective_membership_tier != MEMBERSHIP_TIER_FREE,
            reward_pro_remaining_seconds=remaining_seconds,
        )

    def _next_claim_at(self, today: date) -> datetime:
        next_local = datetime.combine(today + timedelta(days=1), time.min, tzinfo=_REWARD_TZ)
        return next_local.astimezone(timezone.utc)

    def _reward_day(self, value: datetime | None) -> date | None:
        if value is None:
            return None
        return value.astimezone(_REWARD_TZ).date()

    def _normalize_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    async def _ensure_user(self, db: AsyncSession, user_id: str) -> tuple[User, bool]:
        existing = await db.get(User, user_id.strip())
        if existing is not None:
            return existing, False
        return await ensure_user_exists(db, user_id), True

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _non_negative_int(self, value: object) -> int:
        try:
            normalized = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return normalized if normalized > 0 else 0
