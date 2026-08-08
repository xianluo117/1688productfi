"""Add Temu products, ownership, batches, and snapshots.

Revision ID: 20260808_01
Revises: 20260806_01
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op

from app.database import Base
from app import models  # noqa: F401

revision: str = "20260808_01"
down_revision: str | None = "20260806_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = [
        Base.metadata.tables["temu_products"],
        Base.metadata.tables["temu_product_groups"],
        Base.metadata.tables["temu_group_memberships"],
        Base.metadata.tables["temu_collection_batches"],
        Base.metadata.tables["temu_product_snapshots"],
    ]
    for table in tables:
        table.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for table_name in (
        "temu_product_snapshots",
        "temu_collection_batches",
        "temu_group_memberships",
        "temu_product_groups",
        "temu_products",
    ):
        op.drop_table(table_name)
