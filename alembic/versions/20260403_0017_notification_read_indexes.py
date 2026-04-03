"""add unread notification index

Revision ID: 20260403_0017
Revises: 20260328_0016
Create Date: 2026-04-03 13:15:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260403_0017"
down_revision = "20260328_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_notifications_user_unread_created_at",
            "notifications",
            ["user_id", "created_at"],
            unique=False,
            postgresql_where=sa.text("is_read = false"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_notifications_user_unread_created_at",
            table_name="notifications",
            postgresql_concurrently=True,
        )
