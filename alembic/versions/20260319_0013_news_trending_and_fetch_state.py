"""add news trending and fetch state

Revision ID: 20260319_0013
Revises: 20260319_0012
Create Date: 2026-03-19 02:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0013"
down_revision = "20260319_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("news_articles", sa.Column("source_guid", sa.String(length=512), nullable=True))
    op.add_column(
        "news_articles",
        sa.Column("category", sa.String(length=32), nullable=False, server_default="altcoins"),
    )
    op.add_column(
        "news_articles",
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "news_feed_state",
        sa.Column("daily_fetch_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_news_articles_category_published_at",
        "news_articles",
        ["category", "published_at"],
        unique=False,
    )
    op.create_index(
        "ix_news_articles_view_count_published_at",
        "news_articles",
        ["view_count", "published_at"],
        unique=False,
    )
    op.alter_column("news_articles", "category", server_default=None)
    op.alter_column("news_articles", "view_count", server_default=None)
    op.alter_column("news_feed_state", "daily_fetch_count", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_news_articles_view_count_published_at", table_name="news_articles")
    op.drop_index("ix_news_articles_category_published_at", table_name="news_articles")
    op.drop_column("news_feed_state", "daily_fetch_count")
    op.drop_column("news_articles", "view_count")
    op.drop_column("news_articles", "category")
    op.drop_column("news_articles", "source_guid")
