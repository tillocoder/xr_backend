from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LearningTagKey = Literal["strategy", "education", "analysis"]


class LearningVideoLessonResponse(BaseModel):
    id: str
    title: str
    summary: str = ""
    videoUrl: str
    linkUrl: str | None = None
    thumbnailUrl: str | None = None
    tagKey: LearningTagKey = "education"
    durationMinutes: int = 0
    isFeatured: bool = False
    createdAt: datetime | None = None


class AdminLearningVideoLessonResponse(LearningVideoLessonResponse):
    isPublished: bool = False
    sortOrder: int = 0
    updatedAt: datetime | None = None


class LearningVideoLessonUpsertRequest(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    summary: str = Field(default="", max_length=500)
    videoUrl: str = Field(min_length=1, max_length=1024)
    linkUrl: str | None = Field(default=None, max_length=1024)
    thumbnailUrl: str | None = Field(default=None, max_length=1024)
    tagKey: LearningTagKey = "education"
    durationMinutes: int = Field(default=0, ge=0, le=600)
    isFeatured: bool = False
    isPublished: bool = False
    sortOrder: int = Field(default=0, ge=0, le=9999)


class LearningVideoUploadResponse(BaseModel):
    url: str
    path: str
    fileName: str
