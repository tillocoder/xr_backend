"""add AI provider config and news translation tables

Revision ID: 20260315_0008
Revises: 20260314_0007
Create Date: 2026-03-15 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260315_0008"
down_revision = "20260314_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(length=24), nullable=False, server_default="gemini"),
        sa.Column("api_key", sa.String(length=512), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=False, server_default="gemini-1.5-flash"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "news_articles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uid", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("raw_title", sa.String(length=512), nullable=False),
        sa.Column("raw_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_liquidation", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("uid", name="uq_news_articles_uid"),
    )
    op.create_index("ix_news_articles_published_at", "news_articles", ["published_at"])
    op.create_index(
        "ix_news_articles_is_liquidation_published_at",
        "news_articles",
        ["is_liquidation", "published_at"],
    )

    op.create_table(
        "news_article_translations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "article_id",
            sa.Integer(),
            sa.ForeignKey("news_articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "article_id",
            "lang",
            name="uq_news_article_translations_article_id_lang",
        ),
    )
    op.create_index(
        "ix_news_article_translations_article_id",
        "news_article_translations",
        ["article_id"],
    )
    op.create_index(
        "ix_news_article_translations_lang_created_at",
        "news_article_translations",
        ["lang", "created_at"],
    )

    op.create_table(
        "news_feed_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("daily_released_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ingest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cleanup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_translate_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(
        "INSERT INTO news_feed_state (id, date, daily_released_count) VALUES (1, to_char(now() at time zone 'utc', 'YYYY-MM-DD'), 0)"
    )


def downgrade() -> None:
    op.drop_table("news_feed_state")

    op.drop_index("ix_news_article_translations_lang_created_at", table_name="news_article_translations")
    op.drop_index("ix_news_article_translations_article_id", table_name="news_article_translations")
    op.drop_table("news_article_translations")

    op.drop_index("ix_news_articles_is_liquidation_published_at", table_name="news_articles")
    op.drop_index("ix_news_articles_published_at", table_name="news_articles")
    op.drop_table("news_articles")

    op.drop_table("ai_provider_configs")
