from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from urllib.parse import urlparse

from fastapi import HTTPException, status
from fastapi import UploadFile
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    CommunityProfile,
    LearningVideoComment,
    LearningVideoLesson,
    User,
)
from app.schemas.learning import (
    AdminLearningVideoLessonResponse,
    LearningVideoAddCommentRequest,
    LearningVideoCommentResponse,
    LearningVideoLessonResponse,
    LearningVideoPublisherResponse,
    LearningVideoPublishingStatusResponse,
    LearningVideoSelfPublishRequest,
    LearningVideoUploadResponse,
    LearningVideoLessonUpsertRequest,
)
from app.services.community_support import (
    fallback_username,
    normalize_display_name,
    normalize_username,
    public_media_url,
)
from app.services.daily_reward_service import DailyRewardService
from app.services.media_storage import MediaStorageService
from app.services.membership_tiers import (
    MEMBERSHIP_TIER_LEGEND,
    MEMBERSHIP_TIER_PRO,
)
from app.services.rank_theme import resolve_rank_theme


class LearningService:
    _max_upload_bytes = 350 * 1024 * 1024
    _public_cache_ttl = timedelta(seconds=75)
    _default_user_sort_order = 1000
    _publisher_limits = {
        MEMBERSHIP_TIER_PRO: 10,
        MEMBERSHIP_TIER_LEGEND: 200,
    }
    _public_lessons_cache: list[LearningVideoLessonResponse] | None = None
    _public_lessons_cached_at: datetime | None = None

    def __init__(self) -> None:
        self._daily_rewards = DailyRewardService()
        self._media_storage = MediaStorageService()

    async def list_published_video_lessons(
        self,
        db: AsyncSession,
    ) -> list[LearningVideoLessonResponse]:
        cached = self._read_public_cache()
        if cached is not None:
            return cached
        lessons = list(
            (
                await db.scalars(
                    select(LearningVideoLesson)
                    .where(LearningVideoLesson.is_published.is_(True))
                    .order_by(
                        desc(LearningVideoLesson.is_featured),
                        LearningVideoLesson.sort_order.asc(),
                        LearningVideoLesson.created_at.desc(),
                    )
                )
            ).all()
        )
        items = await self._serialize_public_lessons(db, lessons)
        self._write_public_cache(items)
        return [item.model_copy(deep=True) for item in items]

    async def list_all_video_lessons(
        self,
        db: AsyncSession,
    ) -> list[AdminLearningVideoLessonResponse]:
        lessons = list(
            (
                await db.scalars(
                    select(LearningVideoLesson).order_by(
                        desc(LearningVideoLesson.is_published),
                        desc(LearningVideoLesson.is_featured),
                        LearningVideoLesson.sort_order.asc(),
                        LearningVideoLesson.created_at.desc(),
                    )
                )
            ).all()
        )
        publishers = await self._load_publishers_for_lessons(db, lessons)
        return [
            self._to_admin(
                lesson,
                publishers.get(lesson.publisher_uid or ""),
            )
            for lesson in lessons
        ]

    async def get_publishing_status(
        self,
        db: AsyncSession,
        *,
        user_id: str,
    ) -> LearningVideoPublishingStatusResponse:
        user, membership_tier, max_videos, published_count = await self._publisher_context(
            db,
            user_id=user_id,
        )
        del user
        has_access = max_videos > 0
        remaining_videos = max(0, max_videos - published_count) if has_access else 0
        return LearningVideoPublishingStatusResponse(
            membershipTier=membership_tier,
            hasPublishingAccess=has_access,
            canPublishMore=has_access and remaining_videos > 0,
            publishedCount=published_count,
            maxVideos=max_videos,
            remainingVideos=remaining_videos,
        )

    async def create_video_lesson_for_user(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        payload: LearningVideoSelfPublishRequest,
    ) -> LearningVideoLessonResponse:
        user, membership_tier, max_videos, published_count = await self._publisher_context(
            db,
            user_id=user_id,
        )
        self._ensure_publish_capacity(
            membership_tier=membership_tier,
            max_videos=max_videos,
            published_count=published_count,
        )

        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title is required.",
            )

        normalized_video_url = self._normalize_media_or_url(
            payload.videoUrl,
            required=True,
        )
        normalized_thumbnail_url = self._normalize_media_or_url(payload.thumbnailUrl)
        if normalized_thumbnail_url is None:
            normalized_thumbnail_url = self._youtube_thumbnail_url(normalized_video_url)

        lesson = LearningVideoLesson(
            publisher_uid=user.id,
            title=title,
            summary=payload.summary.strip(),
            video_url=normalized_video_url,
            link_url=self._normalize_media_or_url(payload.linkUrl),
            thumbnail_url=normalized_thumbnail_url,
            tag_key=payload.tagKey,
            duration_minutes=payload.durationMinutes,
            is_featured=False,
            is_published=True,
            sort_order=self._default_user_sort_order,
        )
        db.add(lesson)
        await db.commit()
        await db.refresh(lesson)
        self._invalidate_public_cache()
        return self._to_public(
            lesson,
            self._publisher_response(
                user,
                await db.get(CommunityProfile, user.id),
            ),
        )

    async def list_video_comments(
        self,
        db: AsyncSession,
        *,
        lesson_id: str,
    ) -> list[LearningVideoCommentResponse]:
        lesson = await self._get_lesson(db, lesson_id)
        if not lesson.is_published:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Learning video lesson was not found.",
            )
        comments = list(
            (
                await db.scalars(
                    select(LearningVideoComment)
                    .where(LearningVideoComment.lesson_id == lesson.id)
                    .order_by(LearningVideoComment.created_at.desc())
                )
            ).all()
        )
        return await self._serialize_comments(db, comments)

    async def add_video_comment(
        self,
        db: AsyncSession,
        *,
        lesson_id: str,
        user_id: str,
        payload: LearningVideoAddCommentRequest,
    ) -> LearningVideoCommentResponse:
        lesson = await self._get_lesson(db, lesson_id)
        if not lesson.is_published:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Learning video lesson was not found.",
            )
        user = await self._daily_rewards.get_or_create_user(db, user_id=user_id)
        content = payload.content.strip()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Comment cannot be empty.",
            )

        comment = LearningVideoComment(
            lesson_id=lesson.id,
            author_id=user.id,
            content=content,
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)
        return self._to_comment(
            comment,
            self._publisher_response(
                user,
                await db.get(CommunityProfile, user.id),
            ),
        )

    async def create_video_lesson(
        self,
        db: AsyncSession,
        payload: LearningVideoLessonUpsertRequest,
    ) -> AdminLearningVideoLessonResponse:
        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title is required.",
            )

        normalized_video_url = self._normalize_media_or_url(
            payload.videoUrl,
            required=True,
        )
        normalized_thumbnail_url = self._normalize_media_or_url(payload.thumbnailUrl)
        if normalized_thumbnail_url is None:
            normalized_thumbnail_url = self._youtube_thumbnail_url(normalized_video_url)

        lesson = LearningVideoLesson(
            title=title,
            summary=payload.summary.strip(),
            video_url=normalized_video_url,
            link_url=self._normalize_media_or_url(payload.linkUrl),
            thumbnail_url=normalized_thumbnail_url,
            tag_key=payload.tagKey,
            duration_minutes=payload.durationMinutes,
            is_featured=payload.isFeatured,
            is_published=payload.isPublished,
            sort_order=payload.sortOrder,
        )
        db.add(lesson)
        await db.commit()
        await db.refresh(lesson)
        self._invalidate_public_cache()
        return self._to_admin(lesson, None)

    async def update_video_lesson(
        self,
        db: AsyncSession,
        lesson_id: str,
        payload: LearningVideoLessonUpsertRequest,
    ) -> AdminLearningVideoLessonResponse:
        lesson = await self._get_lesson(db, lesson_id)

        title = payload.title.strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title is required.",
            )

        normalized_video_url = self._normalize_media_or_url(
            payload.videoUrl,
            required=True,
        )
        normalized_thumbnail_url = self._normalize_media_or_url(payload.thumbnailUrl)
        if normalized_thumbnail_url is None:
            normalized_thumbnail_url = self._youtube_thumbnail_url(normalized_video_url)

        lesson.title = title
        lesson.summary = payload.summary.strip()
        lesson.video_url = normalized_video_url
        lesson.link_url = self._normalize_media_or_url(payload.linkUrl)
        lesson.thumbnail_url = normalized_thumbnail_url
        lesson.tag_key = payload.tagKey
        lesson.duration_minutes = payload.durationMinutes
        lesson.is_featured = payload.isFeatured
        lesson.is_published = payload.isPublished
        lesson.sort_order = payload.sortOrder

        await db.commit()
        await db.refresh(lesson)
        self._invalidate_public_cache()
        publisher = await self._load_publisher_for_uid(db, lesson.publisher_uid)
        return self._to_admin(lesson, publisher)

    async def set_published(
        self,
        db: AsyncSession,
        lesson_id: str,
        *,
        value: bool,
    ) -> AdminLearningVideoLessonResponse:
        lesson = await self._get_lesson(db, lesson_id)
        lesson.is_published = value
        await db.commit()
        await db.refresh(lesson)
        self._invalidate_public_cache()
        publisher = await self._load_publisher_for_uid(db, lesson.publisher_uid)
        return self._to_admin(lesson, publisher)

    async def set_featured(
        self,
        db: AsyncSession,
        lesson_id: str,
        *,
        value: bool,
    ) -> AdminLearningVideoLessonResponse:
        lesson = await self._get_lesson(db, lesson_id)
        lesson.is_featured = value
        await db.commit()
        await db.refresh(lesson)
        self._invalidate_public_cache()
        publisher = await self._load_publisher_for_uid(db, lesson.publisher_uid)
        return self._to_admin(lesson, publisher)

    async def delete_video_lesson(self, db: AsyncSession, lesson_id: str) -> None:
        lesson = await self._get_lesson(db, lesson_id)
        await self._media_storage.delete(lesson.video_url)
        await db.execute(
            delete(LearningVideoComment).where(
                LearningVideoComment.lesson_id == lesson.id,
            )
        )
        await db.delete(lesson)
        await db.commit()
        self._invalidate_public_cache()

    async def delete_video_lesson_for_user(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        lesson_id: str,
    ) -> None:
        lesson = await self._get_lesson(db, lesson_id)
        if (lesson.publisher_uid or "").strip() != user_id.strip():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete your own learning videos.",
            )
        await self.delete_video_lesson(db, lesson_id)

    async def upload_video_file(self, file: UploadFile) -> LearningVideoUploadResponse:
        raw_name = (file.filename or "").strip()
        file_name = Path(raw_name or "lesson-video.mp4").name
        suffix = Path(file_name).suffix.lower()
        content_type = (file.content_type or "").strip().lower()
        allowed_suffixes = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
        if suffix not in allowed_suffixes and not content_type.startswith("video/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only video uploads are allowed.",
            )

        stored = await self._media_storage.save_upload_file(
            file,
            category="learning-videos",
            default_file_name="lesson-video.mp4",
            max_bytes=self._max_upload_bytes,
            too_large_detail="Video file is too large.",
            empty_detail="Uploaded video file is empty.",
        )
        return LearningVideoUploadResponse(
            url=stored.url,
            path=stored.path,
            fileName=file_name,
        )

    async def _get_lesson(
        self,
        db: AsyncSession,
        lesson_id: str,
    ) -> LearningVideoLesson:
        lesson = await db.get(LearningVideoLesson, lesson_id.strip())
        if lesson is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Learning video lesson was not found.",
            )
        return lesson

    async def _serialize_public_lessons(
        self,
        db: AsyncSession,
        lessons: list[LearningVideoLesson],
    ) -> list[LearningVideoLessonResponse]:
        publishers = await self._load_publishers_for_lessons(db, lessons)
        return [
            self._to_public(
                lesson,
                publishers.get(lesson.publisher_uid or ""),
            )
            for lesson in lessons
        ]

    async def _serialize_comments(
        self,
        db: AsyncSession,
        comments: list[LearningVideoComment],
    ) -> list[LearningVideoCommentResponse]:
        author_ids = sorted({comment.author_id for comment in comments})
        publishers = await self._load_publishers_for_uids(db, author_ids)
        return [
            self._to_comment(comment, publishers.get(comment.author_id))
            for comment in comments
        ]

    async def _load_publishers_for_lessons(
        self,
        db: AsyncSession,
        lessons: list[LearningVideoLesson],
    ) -> dict[str, LearningVideoPublisherResponse]:
        publisher_uids = sorted(
            {
                lesson.publisher_uid.strip()
                for lesson in lessons
                if lesson.publisher_uid is not None and lesson.publisher_uid.strip()
            }
        )
        return await self._load_publishers_for_uids(db, publisher_uids)

    async def _load_publishers_for_uids(
        self,
        db: AsyncSession,
        publisher_uids: list[str],
    ) -> dict[str, LearningVideoPublisherResponse]:
        if not publisher_uids:
            return {}

        users = {
            user.id: user
            for user in (
                await db.scalars(select(User).where(User.id.in_(publisher_uids)))
            ).all()
        }
        profiles = {
            profile.uid: profile
            for profile in (
                await db.scalars(select(CommunityProfile).where(CommunityProfile.uid.in_(publisher_uids)))
            ).all()
        }
        return {
            uid: response
            for uid in publisher_uids
            if (response := self._publisher_response(users.get(uid), profiles.get(uid))) is not None
        }

    async def _load_publisher_for_uid(
        self,
        db: AsyncSession,
        publisher_uid: str | None,
    ) -> LearningVideoPublisherResponse | None:
        normalized_uid = (publisher_uid or "").strip()
        if not normalized_uid:
            return None
        user = await db.get(User, normalized_uid)
        if user is None:
            return None
        profile = await db.get(CommunityProfile, normalized_uid)
        return self._publisher_response(user, profile)

    def _publisher_response(
        self,
        user: User | None,
        profile: CommunityProfile | None,
    ) -> LearningVideoPublisherResponse | None:
        if user is None:
            return None
        profile_username = normalize_username(profile.username) if profile is not None else ""
        profile_display_name = normalize_display_name(profile.display_name) if profile is not None else ""
        display_name = profile_display_name or normalize_display_name(user.display_name) or "XR HODL Member"
        username = profile_username or fallback_username(
            profile_display_name or user.display_name or user.id,
            uid=user.id,
        )
        membership_tier = self._daily_rewards.effective_membership_tier_user(user)
        rank_theme = resolve_rank_theme(
            user_rank_theme=getattr(user, "rank_theme", None),
            profile_rank_theme=getattr(profile, "rank_theme", None) if profile is not None else None,
            membership_tier=membership_tier,
        )
        return LearningVideoPublisherResponse(
            uid=user.id,
            displayName=display_name,
            username=username,
            avatarUrl=public_media_url(
                (
                    getattr(profile, "avatar_url", None)
                    if profile is not None and getattr(profile, "avatar_url", None)
                    else getattr(user, "avatar_url", None)
                ),
                self._public_base_url(),
            ),
            membershipTier=membership_tier,
            isPro=membership_tier != "free",
            rankTheme=rank_theme,
        )

    async def _publisher_context(
        self,
        db: AsyncSession,
        *,
        user_id: str,
    ) -> tuple[User, str, int, int]:
        user = await self._daily_rewards.get_or_create_user(db, user_id=user_id)
        membership_tier = self._daily_rewards.effective_membership_tier_user(user)
        max_videos = self._publisher_limits.get(membership_tier, 0)
        published_count = await self._count_publisher_lessons(db, user.id)
        return user, membership_tier, max_videos, published_count

    async def _count_publisher_lessons(self, db: AsyncSession, publisher_uid: str) -> int:
        count = await db.scalar(
            select(func.count(LearningVideoLesson.id)).where(
                LearningVideoLesson.publisher_uid == publisher_uid.strip()
            )
        )
        return int(count or 0)

    def _ensure_publish_capacity(
        self,
        *,
        membership_tier: str,
        max_videos: int,
        published_count: int,
    ) -> None:
        if max_videos <= 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Pro or Legend members can publish learning videos.",
            )
        if published_count >= max_videos:
            tier_label = "Legend" if membership_tier == MEMBERSHIP_TIER_LEGEND else "Pro"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{tier_label} members can publish up to {max_videos} learning videos.",
            )

    def _to_public(
        self,
        lesson: LearningVideoLesson,
        publisher: LearningVideoPublisherResponse | None,
    ) -> LearningVideoLessonResponse:
        thumbnail_url = self._resolved_thumbnail_url(lesson)
        return LearningVideoLessonResponse(
            id=lesson.id,
            title=lesson.title,
            summary=lesson.summary,
            videoUrl=self._public_media_url(lesson.video_url) or lesson.video_url,
            linkUrl=self._public_media_url(lesson.link_url) or lesson.link_url,
            thumbnailUrl=thumbnail_url,
            tagKey=lesson.tag_key,
            durationMinutes=lesson.duration_minutes,
            isFeatured=lesson.is_featured,
            publisher=publisher.model_copy(deep=True) if publisher is not None else None,
            createdAt=lesson.created_at,
        )

    def _to_admin(
        self,
        lesson: LearningVideoLesson,
        publisher: LearningVideoPublisherResponse | None,
    ) -> AdminLearningVideoLessonResponse:
        thumbnail_url = self._resolved_thumbnail_url(lesson)
        return AdminLearningVideoLessonResponse(
            id=lesson.id,
            title=lesson.title,
            summary=lesson.summary,
            videoUrl=self._public_media_url(lesson.video_url) or lesson.video_url,
            linkUrl=self._public_media_url(lesson.link_url) or lesson.link_url,
            thumbnailUrl=thumbnail_url,
            tagKey=lesson.tag_key,
            durationMinutes=lesson.duration_minutes,
            isFeatured=lesson.is_featured,
            isPublished=lesson.is_published,
            sortOrder=lesson.sort_order,
            publisher=publisher.model_copy(deep=True) if publisher is not None else None,
            createdAt=lesson.created_at,
            updatedAt=lesson.updated_at,
        )

    def _to_comment(
        self,
        comment: LearningVideoComment,
        author: LearningVideoPublisherResponse | None,
    ) -> LearningVideoCommentResponse:
        return LearningVideoCommentResponse(
            id=comment.id,
            lessonId=comment.lesson_id,
            content=comment.content,
            createdAt=comment.created_at,
            author=author.model_copy(deep=True) if author is not None else None,
        )

    def _normalize_media_or_url(self, raw: str | None, *, required: bool = False) -> str | None:
        value = (raw or "").strip()
        if not value:
            if required:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A valid URL is required.",
                )
            return None

        media_reference = self._normalize_media_reference(value)
        if media_reference is not None:
            return media_reference

        value = value.replace("&amp;", "&").replace("&quot;", '"').replace("&apos;", "'")
        if value.startswith("//"):
            value = f"https:{value}"
        elif not re.match(r"^https?://", value, flags=re.IGNORECASE):
            value = f"https://{value}"

        parsed = urlparse(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid HTTP or HTTPS URL is required.",
            )
        return value

    def _normalize_media_reference(self, raw: str | None) -> str | None:
        value = (raw or "").strip()
        if not value:
            return None
        if value.startswith("/media/"):
            return value
        marker = value.find("/media/")
        if marker >= 0:
            return value[marker:]
        return None

    def _public_media_url(self, raw: str | None) -> str | None:
        value = self._normalize_media_reference(raw)
        if value is None:
            normalized = (raw or "").strip()
            return normalized or None

        public_base_url = self._public_base_url()
        if public_base_url is None:
            return value
        return f"{public_base_url}{value}"

    def _resolved_thumbnail_url(self, lesson: LearningVideoLesson) -> str | None:
        if lesson.thumbnail_url:
            return self._public_media_url(lesson.thumbnail_url) or lesson.thumbnail_url
        return self._youtube_thumbnail_url(lesson.video_url)

    def _youtube_thumbnail_url(self, raw: str | None) -> str | None:
        video_id = self._youtube_video_id(raw)
        if video_id is None:
            return None
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    def _youtube_video_id(self, raw: str | None) -> str | None:
        value = (raw or "").strip()
        if not value:
            return None

        parsed = urlparse(value)
        host = parsed.netloc.lower()
        segments = [segment for segment in parsed.path.split("/") if segment]

        if host == "youtu.be" and segments:
            return self._sanitize_youtube_id(segments[0])

        if "youtube.com" in host or "youtube-nocookie.com" in host:
            if parsed.path == "/watch":
                query = dict(
                    part.split("=", 1) if "=" in part else (part, "")
                    for part in parsed.query.split("&")
                    if part
                )
                return self._sanitize_youtube_id(query.get("v"))
            if len(segments) >= 2 and segments[0] in {"embed", "shorts", "live"}:
                return self._sanitize_youtube_id(segments[1])

        return None

    def _sanitize_youtube_id(self, raw: str | None) -> str | None:
        value = (raw or "").strip()
        if not value:
            return None
        cleaned = value.split("&", 1)[0].split("?", 1)[0].split("/", 1)[0]
        if re.match(r"^[A-Za-z0-9_-]{11}$", cleaned):
            return cleaned
        return None

    def _public_base_url(self) -> str | None:
        from app.core.config import get_settings

        raw = get_settings().public_base_url.strip().rstrip("/")
        if raw:
            return raw
        return None

    def _read_public_cache(self) -> list[LearningVideoLessonResponse] | None:
        cached_at = self._public_lessons_cached_at
        cached_items = self._public_lessons_cache
        if cached_at is None or cached_items is None:
            return None
        if datetime.now(timezone.utc) - cached_at > self._public_cache_ttl:
            self._invalidate_public_cache()
            return None
        return [item.model_copy(deep=True) for item in cached_items]

    def _write_public_cache(self, items: list[LearningVideoLessonResponse]) -> None:
        self._public_lessons_cache = [item.model_copy(deep=True) for item in items]
        self._public_lessons_cached_at = datetime.now(timezone.utc)

    def _invalidate_public_cache(self) -> None:
        self._public_lessons_cache = None
        self._public_lessons_cached_at = None
