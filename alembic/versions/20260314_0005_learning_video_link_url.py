"""add learning video link url

Revision ID: 20260314_0005
Revises: 20260313_0004
Create Date: 2026-03-14 16:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260314_0005"
down_revision = "20260313_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_video_lessons",
        sa.Column("link_url", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("learning_video_lessons", "link_url")
