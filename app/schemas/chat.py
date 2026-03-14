from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.feed import FeedAuthor


class ChatMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    replyToMessageId: str | None = None
    messageType: str | None = Field(default=None, max_length=16)
    mediaUrl: str | None = Field(default=None, max_length=512)


class ChatMessageUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class ChatVoiceMessageCreate(BaseModel):
    fileName: str
    contentBase64: str
    durationMs: int = 0
    waveform: list[float] = Field(default_factory=list)
    replyToMessageId: str | None = None


class ChatMessageReplyOut(BaseModel):
    message_id: str
    sender_id: str
    preview_text: str
    message_type: str
    is_deleted: bool = False


class ChatMessageOut(BaseModel):
    id: str
    chat_id: str
    sender_id: str
    recipient_id: str | None = None
    body: str
    message_type: str
    media_url: str | None = None
    media_duration_ms: int = 0
    waveform: list[float] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None
    deleted_at: datetime | None = None
    read_at: datetime | None = None
    reply_to: ChatMessageReplyOut | None = None


class ChatMessagesPage(BaseModel):
    items: list[ChatMessageOut]
    next_cursor: str | None = None
    has_more: bool


class ChatConversationOut(BaseModel):
    chat_id: str
    peer: FeedAuthor
    last_message_text: str
    last_message_sender_uid: str | None = None
    last_message_at: datetime | None = None
    unread_count: int
    updated_at: datetime | None = None


class UnreadCountOut(BaseModel):
    unread_total: int
