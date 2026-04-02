from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from app.services.daily_reward_service import DailyRewardService


class DailyRewardServiceTests(IsolatedAsyncioTestCase):
    async def test_activates_reward_pro_and_resets_balance(self) -> None:
        service = DailyRewardService()
        now = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
        user = _build_user(diamonds_balance=700)
        db = AsyncMock()

        changed = await service._maybe_activate_reward_pro(db, user, now=now)

        self.assertTrue(changed)
        self.assertEqual(user.diamonds_balance, 0)
        self.assertEqual(
            user.reward_pro_expires_at,
            now + timedelta(days=service.reward_pro_duration_days),
        )
        db.flush.assert_awaited_once()

    async def test_extends_existing_reward_pro_and_keeps_remainder(self) -> None:
        service = DailyRewardService()
        now = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
        current_expiry = now + timedelta(days=2)
        user = _build_user(
            diamonds_balance=900,
            reward_pro_expires_at=current_expiry,
        )
        db = AsyncMock()

        changed = await service._maybe_activate_reward_pro(db, user, now=now)

        self.assertTrue(changed)
        self.assertEqual(user.diamonds_balance, 200)
        self.assertEqual(
            user.reward_pro_expires_at,
            current_expiry + timedelta(days=service.reward_pro_duration_days),
        )
        db.flush.assert_awaited_once()

    async def test_consumes_multiple_reward_pro_cycles_in_one_pass(self) -> None:
        service = DailyRewardService()
        now = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
        user = _build_user(diamonds_balance=1500)
        db = AsyncMock()

        changed = await service._maybe_activate_reward_pro(db, user, now=now)

        self.assertTrue(changed)
        self.assertEqual(user.diamonds_balance, 100)
        self.assertEqual(
            user.reward_pro_expires_at,
            now + timedelta(days=service.reward_pro_duration_days * 2),
        )
        db.flush.assert_awaited_once()

    async def test_keeps_balance_untouched_for_paid_membership(self) -> None:
        service = DailyRewardService()
        now = datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
        user = _build_user(
            diamonds_balance=900,
            membership_tier="pro",
            paid_membership_expires_at=now + timedelta(days=10),
            is_pro=True,
        )
        db = AsyncMock()

        changed = await service._maybe_activate_reward_pro(db, user, now=now)

        self.assertFalse(changed)
        self.assertEqual(user.diamonds_balance, 900)
        self.assertIsNone(user.reward_pro_expires_at)
        db.flush.assert_not_awaited()


def _build_user(
    *,
    diamonds_balance: int,
    membership_tier: str = "free",
    paid_membership_expires_at: datetime | None = None,
    reward_pro_expires_at: datetime | None = None,
    is_pro: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        diamonds_balance=diamonds_balance,
        membership_tier=membership_tier,
        paid_membership_expires_at=paid_membership_expires_at,
        reward_pro_expires_at=reward_pro_expires_at,
        is_pro=is_pro,
    )
