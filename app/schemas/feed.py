from datetime import datetime

from pydantic import BaseModel


class FeedAuthor(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None
    membership_tier: str = "free"
    is_pro: bool = False


class FeedPost(BaseModel):
    id: str
    content: str
    comment_count: int
    reaction_counts: dict[str, int]
    created_at: datetime


class FeedViewerState(BaseModel):
    reaction: str | None = None
    is_following_author: bool = False


class FeedItem(BaseModel):
    post: FeedPost
    author: FeedAuthor
    viewer_state: FeedViewerState


class FeedPage(BaseModel):
    items: list[FeedItem]
    next_cursor: str | None = None
    has_more: bool
