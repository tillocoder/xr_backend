from datetime import datetime

from pydantic import BaseModel, Field


class NotificationItemResponse(BaseModel):
    id: str
    kind: str
    event_type: str
    title: str
    body: str
    payload: dict[str, object] = Field(default_factory=dict)
    actor_uid: str | None = None
    actor_display_name: str | None = None
    actor_avatar_url: str | None = None
    post_id: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class NotificationListResponse(BaseModel):
    items: list[NotificationItemResponse] = Field(default_factory=list)
    unread_count: int = 0


class MarkNotificationsReadRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class PushTokenPayload(BaseModel):
    token: str
    platform: str = "unknown"
