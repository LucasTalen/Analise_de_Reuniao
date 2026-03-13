"""Rename analysis_sessions.video_path to source_label

Revision ID: 20260313_0002
Revises: 20260312_0001
Create Date: 2026-03-13 10:15:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260313_0002"
down_revision = "20260312_0001"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _column_names("analysis_sessions")

    if "source_label" in columns:
        return
    if "video_path" not in columns:
        return

    with op.batch_alter_table("analysis_sessions") as batch_op:
        batch_op.alter_column(
            "video_path",
            new_column_name="source_label",
            existing_type=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    columns = _column_names("analysis_sessions")

    if "video_path" in columns:
        return
    if "source_label" not in columns:
        return

    with op.batch_alter_table("analysis_sessions") as batch_op:
        batch_op.alter_column(
            "source_label",
            new_column_name="video_path",
            existing_type=sa.Text(),
            existing_nullable=False,
        )
