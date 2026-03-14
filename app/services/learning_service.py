from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import HTTPException, status
from fastapi import UploadFile
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import LearningVideoLesson
from app.schemas.learning import (
    AdminLearningVideoLessonResponse,
    LearningVideoLessonResponse,
    LearningVideoUploadResponse,
    LearningVideoLessonUpsertRequest,
)


class LearningService:
    _media_root = Path(__file__).resolve().parents[2] / "media"
    _max_upload_bytes = 350 * 1024 * 1024
    _public_cache_ttl = timedelta(seconds=75)
    _public_lessons_cache: list[LearningVideoLessonResponse] | None = None
    _public_lessons_cached_at: datetime | None = None

    async def list_published_video_lessons(
        self,
        db: AsyncSession,
    ) -> list[LearningVideoLessonResponse]:
        cached = self._read_public_cache()
        if cached is not None:
            return cached
        result = await db.scalars(
            select(LearningVideoLesson)
            .where(LearningVideoLesson.is_published.is_(True))
            .order_by(
                desc(LearningVideoLesson.is_featured),
                LearningVideoLesson.sort_order.asc(),
                LearningVideoLesson.created_at.desc(),
            )
        )
        items = [self._to_public(item) for item in result.all()]
        self._write_public_cache(items)
        return [item.model_copy(deep=True) for item in items]

    async def list_all_video_lessons(
        self,
        db: AsyncSession,
    ) -> list[AdminLearningVideoLessonResponse]:
        result = await db.scalars(
            select(LearningVideoLesson).order_by(
                desc(LearningVideoLesson.is_published),
                desc(LearningVideoLesson.is_featured),
                LearningVideoLesson.sort_order.asc(),
                LearningVideoLesson.created_at.desc(),
            )
        )
        return [self._to_admin(item) for item in result.all()]

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
        return self._to_admin(lesson)

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
        return self._to_admin(lesson)

    async def delete_video_lesson(self, db: AsyncSession, lesson_id: str) -> None:
        lesson = await self._get_lesson(db, lesson_id)
        self._delete_local_media_if_managed(lesson.video_url)
        await db.delete(lesson)
        await db.commit()
        self._invalidate_public_cache()

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

        target_dir = self._media_root / "learning-videos"
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid4().hex}{suffix or '.mp4'}"
        target_path = target_dir / stored_name

        written = 0
        try:
            with target_path.open("wb") as buffer:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self._max_upload_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Video file is too large.",
                        )
                    buffer.write(chunk)
        except Exception:
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        if written <= 0:
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded video file is empty.",
            )

        relative_path = f"/media/learning-videos/{stored_name}"
        public_url = self._public_media_url(relative_path) or relative_path
        return LearningVideoUploadResponse(
            url=public_url,
            path=relative_path,
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

    def _to_public(
        self,
        lesson: LearningVideoLesson,
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
            createdAt=lesson.created_at,
        )

    def _to_admin(
        self,
        lesson: LearningVideoLesson,
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
            createdAt=lesson.created_at,
            updatedAt=lesson.updated_at,
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

    def _delete_local_media_if_managed(self, raw: str | None) -> None:
        value = self._normalize_media_reference(raw)
        if value is None or not value.startswith("/media/learning-videos/"):
            return
        relative = value.removeprefix("/media/").strip("/")
        if not relative:
            return
        target = self._media_root / relative
        target.unlink(missing_ok=True)
