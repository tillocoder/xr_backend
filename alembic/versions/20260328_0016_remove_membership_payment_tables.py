"""remove manual membership payment tables

Revision ID: 20260328_0016
Revises: 20260328_0015
Create Date: 2026-03-28 20:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260328_0016"
down_revision = "20260328_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_telegram_payment_admins_active_created_at", table_name="telegram_payment_admins")
    op.drop_table("telegram_payment_admins")

    op.drop_index(op.f("ix_membership_payment_requests_user_id"), table_name="membership_payment_requests")
    op.drop_index("ix_membership_payment_requests_created_at", table_name="membership_payment_requests")
    op.drop_index(
        "ix_membership_payment_requests_telegram_user_status",
        table_name="membership_payment_requests",
    )
    op.drop_index("ix_membership_payment_requests_user_status", table_name="membership_payment_requests")
    op.drop_table("membership_payment_requests")


def downgrade() -> None:
    op.create_table(
        "membership_payment_requests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("checkout_token", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=False),
        sa.Column("price_amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("display_price", sa.String(length=40), nullable=False),
        sa.Column("requested_username", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=32), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=32), nullable=True),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("receipt_kind", sa.String(length=16), nullable=True),
        sa.Column("receipt_file_id", sa.String(length=255), nullable=True),
        sa.Column("receipt_file_unique_id", sa.String(length=255), nullable=True),
        sa.Column("receipt_caption", sa.Text(), nullable=True),
        sa.Column("receipt_message_id", sa.String(length=32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by_chat_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_token"),
    )
    op.create_index(
        "ix_membership_payment_requests_user_status",
        "membership_payment_requests",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_membership_payment_requests_telegram_user_status",
        "membership_payment_requests",
        ["telegram_user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_membership_payment_requests_created_at",
        "membership_payment_requests",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_membership_payment_requests_user_id"),
        "membership_payment_requests",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "telegram_payment_admins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.String(length=32), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=32), nullable=True),
        sa.Column("telegram_username", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index(
        "ix_telegram_payment_admins_active_created_at",
        "telegram_payment_admins",
        ["is_active", "created_at"],
        unique=False,
    )
