"""add Gemini usage scope

Revision ID: 20260408_0020
Revises: 20260407_0019
Create Date: 2026-04-08 18:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260408_0020"
down_revision = "20260407_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_configs",
        sa.Column(
            "usage_scope",
            sa.String(length=24),
            nullable=False,
            server_default="default",
        ),
    )
    op.execute(
        """
        UPDATE ai_provider_configs
        SET usage_scope = 'default'
        WHERE coalesce(trim(usage_scope), '') = ''
        """
    )
    op.create_index(
        "ix_ai_provider_configs_provider_usage_scope_sort_order",
        "ai_provider_configs",
        ["provider", "usage_scope", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_provider_configs_provider_usage_scope_sort_order",
        table_name="ai_provider_configs",
    )
    op.drop_column("ai_provider_configs", "usage_scope")
