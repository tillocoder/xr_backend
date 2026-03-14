"""initial schema

Revision ID: 20260309_0001
Revises:
Create Date: 2026-03-09 15:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260309_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("is_pro", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("watchlist_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("holdings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "community_profiles",
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=24), nullable=False),
        sa.Column("display_name", sa.String(length=32), nullable=False),
        sa.Column("avatar_url", sa.String(length=512), nullable=True),
        sa.Column("cover_image_url", sa.String(length=512), nullable=True),
        sa.Column("biography", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("birthday_label", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("website", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("social_accounts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("public_watchlist_symbols_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("blocked_account_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("username_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("display_name_window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("display_name_change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_pro", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["uid"], ["users.id"]),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index(op.f("ix_community_profiles_username"), "community_profiles", ["username"], unique=True)

    op.create_table(
        "posts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("symbols_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("image_url", sa.String(length=512), nullable=True),
        sa.Column("market_bias", sa.String(length=16), nullable=True),
        sa.Column("poll_options_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("poll_vote_counts_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("poll_vote_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("poll_duration_days", sa.Integer(), nullable=True),
        sa.Column("poll_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_posts_author_id"), "posts", ["author_id"], unique=False)
    op.create_index(op.f("ix_posts_created_at"), "posts", ["created_at"], unique=False)

    op.create_table(
        "chats",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chat_type", sa.String(length=16), nullable=False, server_default="direct"),
        sa.Column("last_message_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "comments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.String(length=32), nullable=False),
        sa.Column("reply_to_comment_id", sa.String(length=32), nullable=True),
        sa.Column("reply_to_author_username", sa.String(length=24), nullable=True),
        sa.Column("content", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["reply_to_comment_id"], ["comments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_comments_post_created_at", "comments", ["post_id", "created_at"], unique=False)
    op.create_index(op.f("ix_comments_author_id"), "comments", ["author_id"], unique=False)
    op.create_index(op.f("ix_comments_post_id"), "comments", ["post_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.String(length=32), nullable=False),
        sa.Column("sender_id", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(length=16), nullable=False, server_default="text"),
        sa.Column("media_url", sa.String(length=512), nullable=True),
        sa.Column("media_duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("waveform_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_chat_created_at", "messages", ["chat_id", "created_at"], unique=False)
    op.create_index(op.f("ix_messages_chat_id"), "messages", ["chat_id"], unique=False)
    op.create_index(op.f("ix_messages_sender_id"), "messages", ["sender_id"], unique=False)

    op.create_foreign_key(
        "fk_chats_last_message_id_messages",
        "chats",
        "messages",
        ["last_message_id"],
        ["id"],
    )

    op.create_table(
        "chat_members",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("last_read_message_id", sa.String(length=32), nullable=True),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"]),
        sa.ForeignKeyConstraint(["last_read_message_id"], ["messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "user_id", name="uq_chat_member_chat_user"),
    )
    op.create_index("ix_chat_members_user_chat", "chat_members", ["user_id", "chat_id"], unique=False)
    op.create_index(op.f("ix_chat_members_chat_id"), "chat_members", ["chat_id"], unique=False)
    op.create_index(op.f("ix_chat_members_user_id"), "chat_members", ["user_id"], unique=False)

    op.create_table(
        "community_follows",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("follower_uid", sa.String(length=32), nullable=False),
        sa.Column("following_uid", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["follower_uid"], ["users.id"]),
        sa.ForeignKeyConstraint(["following_uid"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("follower_uid", "following_uid", name="uq_community_follow_pair"),
    )
    op.create_index("ix_community_follows_following_uid_created_at", "community_follows", ["following_uid", "created_at"], unique=False)
    op.create_index("ix_community_follows_follower_uid_created_at", "community_follows", ["follower_uid", "created_at"], unique=False)
    op.create_index(op.f("ix_community_follows_follower_uid"), "community_follows", ["follower_uid"], unique=False)
    op.create_index(op.f("ix_community_follows_following_uid"), "community_follows", ["following_uid"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_created_at", "notifications", ["user_id", "created_at"], unique=False)
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)

    op.create_table(
        "poll_votes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("option_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_poll_vote_post_user"),
    )
    op.create_index("ix_poll_votes_post_option", "poll_votes", ["post_id", "option_index"], unique=False)
    op.create_index(op.f("ix_poll_votes_post_id"), "poll_votes", ["post_id"], unique=False)
    op.create_index(op.f("ix_poll_votes_user_id"), "poll_votes", ["user_id"], unique=False)

    op.create_table(
        "post_reactions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("reaction_type", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_post_reaction_post_user"),
    )
    op.create_index("ix_post_reactions_post_type", "post_reactions", ["post_id", "reaction_type"], unique=False)
    op.create_index(op.f("ix_post_reactions_post_id"), "post_reactions", ["post_id"], unique=False)
    op.create_index(op.f("ix_post_reactions_user_id"), "post_reactions", ["user_id"], unique=False)

    op.create_table(
        "post_views",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("post_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_post_view_post_user"),
    )
    op.create_index("ix_post_views_post_created_at", "post_views", ["post_id", "created_at"], unique=False)
    op.create_index(op.f("ix_post_views_post_id"), "post_views", ["post_id"], unique=False)
    op.create_index(op.f("ix_post_views_user_id"), "post_views", ["user_id"], unique=False)

    op.create_table(
        "push_tokens",
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("platform", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index("ix_push_tokens_user_id_updated_at", "push_tokens", ["user_id", "updated_at"], unique=False)
    op.create_index(op.f("ix_push_tokens_user_id"), "push_tokens", ["user_id"], unique=False)

    op.create_table(
        "comment_reactions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("comment_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("reaction_code", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comment_id", "user_id", name="uq_comment_reaction_comment_user"),
    )
    op.create_index("ix_comment_reactions_comment_code", "comment_reactions", ["comment_id", "reaction_code"], unique=False)
    op.create_index(op.f("ix_comment_reactions_comment_id"), "comment_reactions", ["comment_id"], unique=False)
    op.create_index(op.f("ix_comment_reactions_user_id"), "comment_reactions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_comment_reactions_user_id"), table_name="comment_reactions")
    op.drop_index(op.f("ix_comment_reactions_comment_id"), table_name="comment_reactions")
    op.drop_index("ix_comment_reactions_comment_code", table_name="comment_reactions")
    op.drop_table("comment_reactions")

    op.drop_index(op.f("ix_push_tokens_user_id"), table_name="push_tokens")
    op.drop_index("ix_push_tokens_user_id_updated_at", table_name="push_tokens")
    op.drop_table("push_tokens")

    op.drop_index(op.f("ix_post_views_user_id"), table_name="post_views")
    op.drop_index(op.f("ix_post_views_post_id"), table_name="post_views")
    op.drop_index("ix_post_views_post_created_at", table_name="post_views")
    op.drop_table("post_views")

    op.drop_index(op.f("ix_post_reactions_user_id"), table_name="post_reactions")
    op.drop_index(op.f("ix_post_reactions_post_id"), table_name="post_reactions")
    op.drop_index("ix_post_reactions_post_type", table_name="post_reactions")
    op.drop_table("post_reactions")

    op.drop_index(op.f("ix_poll_votes_user_id"), table_name="poll_votes")
    op.drop_index(op.f("ix_poll_votes_post_id"), table_name="poll_votes")
    op.drop_index("ix_poll_votes_post_option", table_name="poll_votes")
    op.drop_table("poll_votes")

    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index("ix_notifications_user_created_at", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(op.f("ix_community_follows_following_uid"), table_name="community_follows")
    op.drop_index(op.f("ix_community_follows_follower_uid"), table_name="community_follows")
    op.drop_index("ix_community_follows_follower_uid_created_at", table_name="community_follows")
    op.drop_index("ix_community_follows_following_uid_created_at", table_name="community_follows")
    op.drop_table("community_follows")

    op.drop_index(op.f("ix_chat_members_user_id"), table_name="chat_members")
    op.drop_index(op.f("ix_chat_members_chat_id"), table_name="chat_members")
    op.drop_index("ix_chat_members_user_chat", table_name="chat_members")
    op.drop_table("chat_members")

    op.drop_constraint("fk_chats_last_message_id_messages", "chats", type_="foreignkey")
    op.drop_index(op.f("ix_messages_sender_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_chat_id"), table_name="messages")
    op.drop_index("ix_messages_chat_created_at", table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_comments_post_id"), table_name="comments")
    op.drop_index(op.f("ix_comments_author_id"), table_name="comments")
    op.drop_index("ix_comments_post_created_at", table_name="comments")
    op.drop_table("comments")

    op.drop_table("chats")

    op.drop_index(op.f("ix_posts_created_at"), table_name="posts")
    op.drop_index(op.f("ix_posts_author_id"), table_name="posts")
    op.drop_table("posts")

    op.drop_index(op.f("ix_community_profiles_username"), table_name="community_profiles")
    op.drop_table("community_profiles")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
