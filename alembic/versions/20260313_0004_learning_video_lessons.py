"""add learning video lessons

Revision ID: 20260313_0004
Revises: 20260311_0003
Create Date: 2026-03-13 02:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260313_0004"
down_revision = "20260311_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_video_lessons",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=140), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("video_url", sa.String(length=1024), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=1024), nullable=True),
        sa.Column("tag_key", sa.String(length=24), nullable=False, server_default="education"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_video_lessons_published_sort",
        "learning_video_lessons",
        ["is_published", "is_featured", "sort_order", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_learning_video_lessons_published_sort", table_name="learning_video_lessons")
    op.drop_table("learning_video_lessons")
