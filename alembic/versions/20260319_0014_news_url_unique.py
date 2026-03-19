"""make news url unique

Revision ID: 20260319_0014
Revises: 20260319_0013
Create Date: 2026-03-19 03:05:00
"""

from __future__ import annotations

from alembic import op


revision = "20260319_0014"
down_revision = "20260319_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ux_news_articles_url", "news_articles", ["url"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_news_articles_url", table_name="news_articles")
