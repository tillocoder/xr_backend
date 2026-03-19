"""add notification state for released news

Revision ID: 20260319_0012
Revises: 20260319_0011
Create Date: 2026-03-19 01:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0012"
down_revision = "20260319_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("news_articles", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_news_articles_released_at_notified_at",
        "news_articles",
        ["released_at", "notified_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            UPDATE news_articles
            SET notified_at = COALESCE(released_at, created_at)
            WHERE released_at IS NOT NULL AND notified_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_news_articles_released_at_notified_at", table_name="news_articles")
    op.drop_column("news_articles", "notified_at")
