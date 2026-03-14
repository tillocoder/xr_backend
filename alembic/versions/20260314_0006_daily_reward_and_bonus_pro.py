"""add daily reward and bonus pro fields

Revision ID: 20260314_0006
Revises: 20260314_0005
Create Date: 2026-03-14 18:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260314_0006"
down_revision = "20260314_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "diamonds_balance",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "daily_reward_streak",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("daily_reward_last_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("reward_pro_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "reward_pro_expires_at")
    op.drop_column("users", "daily_reward_last_claimed_at")
    op.drop_column("users", "daily_reward_streak")
    op.drop_column("users", "diamonds_balance")
