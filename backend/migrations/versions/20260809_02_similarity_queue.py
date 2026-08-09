"""Add similarity queue controls.

Revision ID: 20260809_02
Revises: 20260809_01
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "20260809_02"
down_revision: str | None = "20260809_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("similarity_queue_control"):
        op.create_table(
            "similarity_queue_control",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(length=20), nullable=False, server_default="running"),
            sa.Column("concurrency", sa.Integer(), nullable=False, server_default="4"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
    op.execute(
        "INSERT INTO similarity_queue_control (id, state, concurrency) "
        "VALUES (1, 'running', 4) ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("similarity_queue_control")
