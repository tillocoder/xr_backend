from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException

from app.services.learning_service import LearningService
from app.services.membership_tiers import (
    MEMBERSHIP_TIER_FREE,
    MEMBERSHIP_TIER_LEGEND,
    MEMBERSHIP_TIER_PRO,
)


class LearningServiceTests(IsolatedAsyncioTestCase):
    async def test_get_publishing_status_returns_pro_limit_and_remaining_slots(self) -> None:
        service = LearningService()
        user = _build_user(user_id="pro-user", membership_tier=MEMBERSHIP_TIER_PRO, is_pro=True)
        service._daily_rewards = SimpleNamespace(
            get_or_create_user=AsyncMock(return_value=user),
            effective_membership_tier_user=Mock(return_value=MEMBERSHIP_TIER_PRO),
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=3))

        status = await service.get_publishing_status(db, user_id=user.id)

        self.assertEqual(status.membershipTier, MEMBERSHIP_TIER_PRO)
        self.assertTrue(status.hasPublishingAccess)
        self.assertTrue(status.canPublishMore)
        self.assertEqual(status.publishedCount, 3)
        self.assertEqual(status.maxVideos, 10)
        self.assertEqual(status.remainingVideos, 7)

    async def test_get_publishing_status_denies_free_users(self) -> None:
        service = LearningService()
        user = _build_user(user_id="free-user", membership_tier=MEMBERSHIP_TIER_FREE, is_pro=False)
        service._daily_rewards = SimpleNamespace(
            get_or_create_user=AsyncMock(return_value=user),
            effective_membership_tier_user=Mock(return_value=MEMBERSHIP_TIER_FREE),
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=0))

        status = await service.get_publishing_status(db, user_id=user.id)

        self.assertEqual(status.membershipTier, MEMBERSHIP_TIER_FREE)
        self.assertFalse(status.hasPublishingAccess)
        self.assertFalse(status.canPublishMore)
        self.assertEqual(status.maxVideos, 0)
        self.assertEqual(status.remainingVideos, 0)

    async def test_create_video_lesson_for_user_rejects_when_pro_limit_is_reached(self) -> None:
        service = LearningService()
        user = _build_user(user_id="pro-user", membership_tier=MEMBERSHIP_TIER_PRO, is_pro=True)
        service._daily_rewards = SimpleNamespace(
            get_or_create_user=AsyncMock(return_value=user),
            effective_membership_tier_user=Mock(return_value=MEMBERSHIP_TIER_PRO),
        )
        db = SimpleNamespace(
            scalar=AsyncMock(return_value=10),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            get=AsyncMock(return_value=None),
        )

        with self.assertRaises(HTTPException) as context:
            await service.create_video_lesson_for_user(
                db,
                user_id=user.id,
                payload=SimpleNamespace(
                    title="A title",
                    summary="A summary",
                    videoUrl="https://youtube.com/watch?v=dQw4w9WgXcQ",
                    linkUrl=None,
                    thumbnailUrl=None,
                    tagKey="education",
                    durationMinutes=8,
                ),
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("up to 10 learning videos", context.exception.detail)
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_get_publishing_status_returns_legend_limit(self) -> None:
        service = LearningService()
        user = _build_user(
            user_id="legend-user",
            membership_tier=MEMBERSHIP_TIER_LEGEND,
            is_pro=True,
        )
        service._daily_rewards = SimpleNamespace(
            get_or_create_user=AsyncMock(return_value=user),
            effective_membership_tier_user=Mock(return_value=MEMBERSHIP_TIER_LEGEND),
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=42))

        status = await service.get_publishing_status(db, user_id=user.id)

        self.assertEqual(status.membershipTier, MEMBERSHIP_TIER_LEGEND)
        self.assertTrue(status.hasPublishingAccess)
        self.assertEqual(status.maxVideos, 200)
        self.assertEqual(status.remainingVideos, 158)

    async def test_add_video_comment_creates_comment_for_published_lesson(self) -> None:
        service = LearningService()
        user = _build_user(user_id="pro-user", membership_tier=MEMBERSHIP_TIER_PRO, is_pro=True)
        lesson = SimpleNamespace(id="lesson-1", is_published=True)
        profile = SimpleNamespace(
            username="maker",
            display_name="Maker",
            avatar_url=None,
        )

        async def get_side_effect(model, key):
            if key == lesson.id:
                return lesson
            if key == user.id:
                return profile
            return None

        async def refresh_side_effect(comment):
            comment.id = "comment-1"
            comment.created_at = datetime(2026, 4, 7, tzinfo=timezone.utc)

        service._daily_rewards = SimpleNamespace(
            get_or_create_user=AsyncMock(return_value=user),
            effective_membership_tier_user=Mock(return_value=MEMBERSHIP_TIER_PRO),
        )
        db = SimpleNamespace(
            get=AsyncMock(side_effect=get_side_effect),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(side_effect=refresh_side_effect),
        )

        comment = await service.add_video_comment(
            db,
            lesson_id=lesson.id,
            user_id=user.id,
            payload=SimpleNamespace(content=" Great lesson "),
        )

        self.assertEqual(comment.id, "comment-1")
        self.assertEqual(comment.lessonId, lesson.id)
        self.assertEqual(comment.content, "Great lesson")
        self.assertEqual(comment.author.uid, user.id)
        self.assertEqual(comment.author.displayName, "Maker")
        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_list_video_comments_rejects_unpublished_lesson(self) -> None:
        service = LearningService()
        lesson = SimpleNamespace(id="lesson-1", is_published=False)
        db = SimpleNamespace(get=AsyncMock(return_value=lesson))

        with self.assertRaises(HTTPException) as context:
            await service.list_video_comments(db, lesson_id=lesson.id)

        self.assertEqual(context.exception.status_code, 404)
        self.assertIn("was not found", context.exception.detail)


def _build_user(*, user_id: str, membership_tier: str, is_pro: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        membership_tier=membership_tier,
        is_pro=is_pro,
        reward_pro_expires_at=None,
        paid_membership_expires_at=None,
        display_name="XR Member",
        avatar_url=None,
    )
