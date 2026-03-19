"""add Gemini key pool metadata

Revision ID: 20260319_0010
Revises: 20260318_0009
Create Date: 2026-03-19 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0010"
down_revision = "20260318_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_configs",
        sa.Column("label", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "ai_provider_configs",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (ORDER BY id) AS rn
            FROM ai_provider_configs
            WHERE provider = 'gemini'
        )
        UPDATE ai_provider_configs cfg
        SET
            sort_order = ordered.rn,
            label = CASE
                WHEN coalesce(cfg.label, '') = '' THEN 'Gemini Key ' || ordered.rn::text
                ELSE cfg.label
            END
        FROM ordered
        WHERE cfg.id = ordered.id
        """
    )


def downgrade() -> None:
    op.drop_column("ai_provider_configs", "sort_order")
    op.drop_column("ai_provider_configs", "label")
