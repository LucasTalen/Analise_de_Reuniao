"""Initial BYOK schema

Revision ID: 20260312_0001
Revises: 
Create Date: 2026-03-12 12:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260312_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
    )

    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("key_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_last4", sa.String(8), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("rotated_at", sa.Integer(), nullable=True),
    )

    op.create_index(
        "idx_user_api_keys_active",
        "user_api_keys",
        ["user_id", "provider"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "analysis_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_path", sa.Text(), nullable=False),
        sa.Column("transcription_json", sa.Text(), nullable=False),
        sa.Column("history_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )
    op.create_index(
        "idx_analysis_sessions_user",
        "analysis_sessions",
        ["user_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(), sa.ForeignKey("analysis_sessions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_index(
        "idx_usage_events_user",
        "usage_events",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_usage_events_user", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_index("idx_analysis_sessions_user", table_name="analysis_sessions")
    op.drop_table("analysis_sessions")

    op.drop_index("idx_user_api_keys_active", table_name="user_api_keys")
    op.drop_table("user_api_keys")

    op.drop_table("users")
