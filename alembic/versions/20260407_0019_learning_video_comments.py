"""add learning video comments

Revision ID: 20260407_0019
Revises: 20260406_0018
Create Date: 2026-04-07 01:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260407_0019"
down_revision = "20260406_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_video_comments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("lesson_id", sa.String(length=32), nullable=False),
        sa.Column("author_id", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(length=600), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["lesson_id"], ["learning_video_lessons.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_video_comments_author_id",
        "learning_video_comments",
        ["author_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_video_comments_lesson_id",
        "learning_video_comments",
        ["lesson_id"],
        unique=False,
    )
    op.create_index(
        "ix_learning_video_comments_lesson_created_at",
        "learning_video_comments",
        ["lesson_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_learning_video_comments_lesson_created_at",
        table_name="learning_video_comments",
    )
    op.drop_index(
        "ix_learning_video_comments_lesson_id",
        table_name="learning_video_comments",
    )
    op.drop_index(
        "ix_learning_video_comments_author_id",
        table_name="learning_video_comments",
    )
    op.drop_table("learning_video_comments")
