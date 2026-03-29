from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    membership_tier: Mapped[str] = mapped_column(String(16), default="free", nullable=False)
    is_pro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paid_membership_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_membership_plan_code: Mapped[str | None] = mapped_column(String(32))
    diamonds_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_reward_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_reward_last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reward_pro_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watchlist_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    holdings_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    linked_wallets_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CommunityProfile(Base):
    __tablename__ = "community_profiles"

    uid: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    username: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(32))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    cover_image_url: Mapped[str | None] = mapped_column(String(512))
    biography: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    birthday_label: Mapped[str] = mapped_column(String(24), default="", nullable=False)
    website: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    social_accounts_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    public_watchlist_symbols_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    blocked_account_ids_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    username_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name_change_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_pro: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LearningVideoLesson(Base):
    __tablename__ = "learning_video_lessons"
    __table_args__ = (
        Index(
            "ix_learning_video_lessons_published_sort",
            "is_published",
            "is_featured",
            "sort_order",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(140))
    summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    video_url: Mapped[str] = mapped_column(String(1024))
    link_url: Mapped[str | None] = mapped_column(String(1024))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))
    tag_key: Mapped[str] = mapped_column(String(24), default="education", nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str | None] = mapped_column(String(16))
    symbols_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512))
    market_bias: Mapped[str | None] = mapped_column(String(16))
    poll_options_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    poll_vote_counts_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    poll_vote_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    poll_duration_days: Mapped[int | None] = mapped_column(Integer)
    poll_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostReaction(Base):
    __tablename__ = "post_reactions"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_reaction_post_user"),
        Index("ix_post_reactions_post_type", "post_id", "reaction_type"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reaction_type: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PostView(Base):
    __tablename__ = "post_views"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_view_post_user"),
        Index("ix_post_views_post_created_at", "post_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PollVote(Base):
    __tablename__ = "poll_votes"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_poll_vote_post_user"),
        Index("ix_poll_votes_post_option", "post_id", "option_index"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    option_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_post_created_at", "post_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reply_to_comment_id: Mapped[str | None] = mapped_column(ForeignKey("comments.id"))
    reply_to_author_username: Mapped[str | None] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommentReaction(Base):
    __tablename__ = "comment_reactions"
    __table_args__ = (
        UniqueConstraint("comment_id", "user_id", name="uq_comment_reaction_comment_user"),
        Index("ix_comment_reactions_comment_code", "comment_id", "reaction_code"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    comment_id: Mapped[str] = mapped_column(ForeignKey("comments.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reaction_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CommunityFollow(Base):
    __tablename__ = "community_follows"
    __table_args__ = (
        UniqueConstraint("follower_uid", "following_uid", name="uq_community_follow_pair"),
        Index("ix_community_follows_following_uid_created_at", "following_uid", "created_at"),
        Index("ix_community_follows_follower_uid_created_at", "follower_uid", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    follower_uid: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    following_uid: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    chat_type: Mapped[str] = mapped_column(String(16), default="direct", nullable=False)
    last_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_chat_member_chat_user"),
        Index("ix_chat_members_user_chat", "user_id", "chat_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    last_read_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_chat_created_at", "chat_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    chat_id: Mapped[str] = mapped_column(ForeignKey("chats.id"), index=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    media_url: Mapped[str | None] = mapped_column(String(512))
    media_duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    waveform_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created_at", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict] = mapped_column(JSONB)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PushToken(Base):
    __tablename__ = "push_tokens"
    __table_args__ = (
        Index("ix_push_tokens_user_id_updated_at", "user_id", "updated_at"),
    )

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_access_token_hash", "access_token_hash"),
        Index("ix_auth_sessions_refresh_token_hash", "refresh_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    access_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiProviderConfig(Base):
    __tablename__ = "ai_provider_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(24), default="gemini", nullable=False)
    label: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(512))
    model: Mapped[str] = mapped_column(String(64), default="gemini-3-flash-preview", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("uid", name="uq_news_articles_uid"),
        Index("ux_news_articles_url", "url", unique=True),
        Index("ix_news_articles_published_at", "published_at"),
        Index("ix_news_articles_is_liquidation_published_at", "is_liquidation", "published_at"),
        Index("ix_news_articles_released_at_notified_at", "released_at", "notified_at"),
        Index("ix_news_articles_category_published_at", "category", "published_at"),
        Index("ix_news_articles_view_count_published_at", "view_count", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_guid: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    raw_title: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_url: Mapped[str | None] = mapped_column(String(1024))
    images_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    content_blocks_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category: Mapped[str] = mapped_column(String(32), default="altcoins", nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_liquidation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NewsArticleTranslation(Base):
    __tablename__ = "news_article_translations"
    __table_args__ = (
        UniqueConstraint("article_id", "lang", name="uq_news_article_translations_article_id_lang"),
        Index("ix_news_article_translations_lang_created_at", "lang", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("news_articles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lang: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_blocks_json: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketCoin(Base):
    __tablename__ = "market_coins"
    __table_args__ = (
        Index("ix_market_coins_symbol", "symbol"),
        Index("ix_market_coins_rank", "market_cap_rank"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    market_cap_rank: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MarketPricePoint(Base):
    __tablename__ = "market_price_points"
    __table_args__ = (
        Index("ix_market_price_points_coin_captured_at", "coin_id", "captured_at"),
        Index("ix_market_price_points_captured_at", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coin_id: Mapped[str] = mapped_column(ForeignKey("market_coins.id"), index=True)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    change_24h: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    quote_volume_usd: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    market_cap_usd: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="coingecko", nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserTargetAlert(Base):
    __tablename__ = "user_target_alerts"
    __table_args__ = (
        Index("ix_user_target_alerts_user_active", "user_id", "is_active"),
        Index("ix_user_target_alerts_coin_active", "coin_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    coin_id: Mapped[str] = mapped_column(ForeignKey("market_coins.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String(12), default="above", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MarketAlertEvent(Base):
    __tablename__ = "market_alert_events"
    __table_args__ = (
        Index("ix_market_alert_events_kind_created", "kind", "created_at"),
        Index("ix_market_alert_events_dedupe", "dedupe_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    coin_id: Mapped[str | None] = mapped_column(ForeignKey("market_coins.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    body: Mapped[str] = mapped_column(String(400), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NewsFeedState(Base):
    __tablename__ = "news_feed_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    daily_released_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    daily_fetch_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ingest_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_cleanup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_translate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

