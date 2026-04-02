from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from app.services.user_service import ensure_user_exists


class EnsureUserExistsTests(IsolatedAsyncioTestCase):
    async def test_returns_existing_user_without_overwriting_membership(self) -> None:
        existing = SimpleNamespace(
            id="u-1",
            membership_tier="legend",
            is_pro=True,
            display_name="Legend User",
            avatar_url="https://example.com/avatar.png",
        )
        db = SimpleNamespace(
            get=AsyncMock(return_value=existing),
            add=Mock(),
            flush=AsyncMock(),
        )

        user = await ensure_user_exists(
            db,
            "u-1",
            display_name="New Name",
            avatar_url="https://example.com/new.png",
            is_pro=False,
        )

        self.assertIs(user, existing)
        self.assertEqual(user.membership_tier, "legend")
        self.assertTrue(user.is_pro)
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    async def test_new_user_defaults_to_free_when_is_pro_not_set(self) -> None:
        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            add=Mock(),
            flush=AsyncMock(),
        )

        user = await ensure_user_exists(
            db,
            "new-user",
            display_name="",
            avatar_url="",
            is_pro=None,
        )

        self.assertEqual(user.id, "new-user")
        self.assertEqual(user.membership_tier, "free")
        self.assertFalse(user.is_pro)
        db.add.assert_called_once()
        db.flush.assert_awaited_once()
