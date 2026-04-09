"""add learning video publishers

Revision ID: 20260406_0018
Revises: 20260403_0017
Create Date: 2026-04-06 18:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_0018"
down_revision = "20260403_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_video_lessons",
        sa.Column("publisher_uid", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_learning_video_lessons_publisher_uid",
        "learning_video_lessons",
        ["publisher_uid"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_learning_video_lessons_publisher_uid_users",
        "learning_video_lessons",
        "users",
        ["publisher_uid"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_learning_video_lessons_publisher_uid_users",
        "learning_video_lessons",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_learning_video_lessons_publisher_uid",
        table_name="learning_video_lessons",
    )
    op.drop_column("learning_video_lessons", "publisher_uid")
