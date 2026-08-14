"""Add per-user Temu product favorites.

Revision ID: 20260813_01
Revises: 20260812_02
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_01"
down_revision: str | None = "20260812_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "temu_product_favorites" in table_names():
        return
    op.create_table(
        "temu_product_favorites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["admin_users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["temu_products.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "product_id", name="uq_temu_favorite_owner_product"
        ),
    )
    op.create_index(
        "ix_temu_product_favorites_owner_id",
        "temu_product_favorites",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_temu_product_favorites_product_id",
        "temu_product_favorites",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_temu_product_favorites_created_at",
        "temu_product_favorites",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    if "temu_product_favorites" not in table_names():
        return
    op.drop_index(
        "ix_temu_product_favorites_created_at",
        table_name="temu_product_favorites",
    )
    op.drop_index(
        "ix_temu_product_favorites_product_id",
        table_name="temu_product_favorites",
    )
    op.drop_index(
        "ix_temu_product_favorites_owner_id",
        table_name="temu_product_favorites",
    )
    op.drop_table("temu_product_favorites")
