"""Add automatic vectorization switch to similarity queue control.

Revision ID: 20260810_01
Revises: 20260809_02
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260810_01"
down_revision: str | None = "20260809_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in inspect(bind).get_columns("similarity_queue_control")
    }
    if "auto_vectorization_enabled" not in columns:
        op.add_column(
            "similarity_queue_control",
            sa.Column(
                "auto_vectorization_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {
        column["name"]
        for column in inspect(bind).get_columns("similarity_queue_control")
    }
    if "auto_vectorization_enabled" in columns:
        op.drop_column(
            "similarity_queue_control",
            "auto_vectorization_enabled",
        )
