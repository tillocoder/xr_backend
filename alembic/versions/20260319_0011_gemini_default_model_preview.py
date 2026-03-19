"""set Gemini default model to preview

Revision ID: 20260319_0011
Revises: 20260319_0010
Create Date: 2026-03-19 00:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0011"
down_revision = "20260319_0010"
branch_labels = None
depends_on = None


OLD_DEFAULT_MODEL = "gemini-1.5-flash"
NEW_DEFAULT_MODEL = "gemini-3-flash-preview"


def upgrade() -> None:
    op.alter_column(
        "ai_provider_configs",
        "model",
        existing_type=sa.String(length=64),
        server_default=NEW_DEFAULT_MODEL,
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            """
            UPDATE ai_provider_configs
            SET model = :new_model
            WHERE provider = 'gemini' AND model = :old_model
            """
        ).bindparams(new_model=NEW_DEFAULT_MODEL, old_model=OLD_DEFAULT_MODEL)
    )


def downgrade() -> None:
    op.alter_column(
        "ai_provider_configs",
        "model",
        existing_type=sa.String(length=64),
        server_default=OLD_DEFAULT_MODEL,
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            """
            UPDATE ai_provider_configs
            SET model = :old_model
            WHERE provider = 'gemini' AND model = :new_model
            """
        ).bindparams(new_model=NEW_DEFAULT_MODEL, old_model=OLD_DEFAULT_MODEL)
    )
