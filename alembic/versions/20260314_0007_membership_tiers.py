"""add membership tier to users

Revision ID: 20260314_0007
Revises: 20260314_0006
Create Date: 2026-03-14 21:35:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260314_0007"
down_revision = "20260314_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "membership_tier",
            sa.String(length=16),
            nullable=False,
            server_default="free",
        ),
    )
    op.execute("UPDATE users SET membership_tier = 'pro' WHERE is_pro = true")


def downgrade() -> None:
    op.drop_column("users", "membership_tier")
