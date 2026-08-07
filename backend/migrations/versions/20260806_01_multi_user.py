"""Add multi-user ownership and personal ingest tokens.

Revision ID: 20260806_01
Revises:
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260806_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def _unique_constraints(table_name: str) -> list[dict]:
    return inspect(op.get_bind()).get_unique_constraints(table_name)


def upgrade() -> None:
    if not _has_table("admin_users"):
        from app.database import Base
        from app import models  # noqa: F401

        Base.metadata.create_all(bind=op.get_bind())
        return

    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.add_column(sa.Column("role", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("ingest_token_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("ingest_token_prefix", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("ingest_token_updated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(sa.text("UPDATE admin_users SET role = 'admin', is_active = true"))
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.alter_column("role", nullable=False, server_default="user")
        batch_op.alter_column("is_active", nullable=False, server_default=sa.true())
        batch_op.create_unique_constraint("uq_admin_users_ingest_token_hash", ["ingest_token_hash"])
        batch_op.create_index("ix_admin_users_role", ["role"])
        batch_op.create_index("ix_admin_users_is_active", ["is_active"])
        batch_op.create_index("ix_admin_users_ingest_token_hash", ["ingest_token_hash"])

    admin_id = op.get_bind().execute(
        sa.text("SELECT id FROM admin_users ORDER BY id LIMIT 1")
    ).scalar_one()

    for table_name in ("product_groups", "product_group_memberships", "collection_batches", "product_snapshots"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        op.execute(sa.text(f"UPDATE {table_name} SET owner_id = :admin_id").bindparams(admin_id=admin_id))
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column("owner_id", nullable=False)
            batch_op.create_foreign_key(
                f"fk_{table_name}_owner_id_admin_users",
                "admin_users",
                ["owner_id"],
                ["id"],
                ondelete="CASCADE",
            )
            batch_op.create_index(f"ix_{table_name}_owner_id", ["owner_id"])

    for constraint in _unique_constraints("product_groups"):
        if constraint.get("column_names") == ["normalized_name"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "product_groups", type_="unique")
    op.create_unique_constraint(
        "uq_product_group_owner_name",
        "product_groups",
        ["owner_id", "normalized_name"],
    )

    for constraint in _unique_constraints("collection_batches"):
        if constraint.get("column_names") == ["idempotency_key"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "collection_batches", type_="unique")
    op.create_unique_constraint(
        "uq_collection_batch_owner_key",
        "collection_batches",
        ["owner_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_owner_product_group_membership",
        "product_group_memberships",
        ["owner_id", "product_id", "group_id"],
    )


def downgrade() -> None:
    if not _has_table("admin_users"):
        return

    op.drop_constraint("uq_owner_product_group_membership", "product_group_memberships", type_="unique")
    op.drop_constraint("uq_collection_batch_owner_key", "collection_batches", type_="unique")
    op.create_unique_constraint(
        "uq_collection_batch_idempotency_key",
        "collection_batches",
        ["idempotency_key"],
    )
    op.drop_constraint("uq_product_group_owner_name", "product_groups", type_="unique")
    op.create_unique_constraint(
        "uq_product_groups_normalized_name",
        "product_groups",
        ["normalized_name"],
    )

    for table_name in ("product_snapshots", "collection_batches", "product_group_memberships", "product_groups"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_owner_id")
            batch_op.drop_constraint(f"fk_{table_name}_owner_id_admin_users", type_="foreignkey")
            batch_op.drop_column("owner_id")

    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.drop_index("ix_admin_users_ingest_token_hash")
        batch_op.drop_index("ix_admin_users_is_active")
        batch_op.drop_index("ix_admin_users_role")
        batch_op.drop_constraint("uq_admin_users_ingest_token_hash", type_="unique")
        batch_op.drop_column("ingest_token_updated_at")
        batch_op.drop_column("ingest_token_prefix")
        batch_op.drop_column("ingest_token_hash")
        batch_op.drop_column("is_active")
        batch_op.drop_column("role")
