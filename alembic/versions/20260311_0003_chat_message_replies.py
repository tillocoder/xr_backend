"""chat message reply reference

Revision ID: 20260311_0003
Revises: 20260310_0002
Create Date: 2026-03-11 01:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260311_0003"
down_revision = "20260310_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("reply_to_message_id", sa.String(length=32), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_reply_to_message_id",
        "messages",
        "messages",
        ["reply_to_message_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_messages_reply_to_message_id", "messages", type_="foreignkey")
    op.drop_column("messages", "reply_to_message_id")
