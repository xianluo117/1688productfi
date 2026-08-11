"""Add Temu and cross-platform image similarity support.

Revision ID: 20260811_01
Revises: 20260810_02
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from app import models  # noqa: F401
from app.database import Base

revision: str = "20260811_01"
down_revision: str | None = "20260810_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    temu_columns = {column["name"] for column in inspect(bind).get_columns("temu_products")}
    additions = (
        ("cached_image_name", sa.Column("cached_image_name", sa.String(length=255), nullable=True)),
        ("image_status", sa.Column("image_status", sa.String(length=20), nullable=False, server_default="pending")),
        ("image_content_hash", sa.Column("image_content_hash", sa.String(length=64), nullable=True)),
        ("image_phash", sa.Column("image_phash", sa.String(length=16), nullable=True)),
        ("similarity_status", sa.Column("similarity_status", sa.String(length=20), nullable=False, server_default="pending")),
    )
    for name, column in additions:
        if name not in temu_columns:
            op.add_column("temu_products", column)
    for name in ("image_content_hash", "image_phash", "similarity_status"):
        op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS ix_temu_products_{name} ON temu_products ({name})"))

    audit_columns = {column["name"] for column in inspect(bind).get_columns("similarity_audit_logs")}
    if "candidate_type" not in audit_columns:
        op.add_column(
            "similarity_audit_logs",
            sa.Column("candidate_type", sa.String(length=30), nullable=False, server_default="1688_same"),
        )
        op.create_index("ix_similarity_audit_logs_candidate_type", "similarity_audit_logs", ["candidate_type"])
    if "external_candidate_id" not in audit_columns:
        op.add_column("similarity_audit_logs", sa.Column("external_candidate_id", sa.Integer(), nullable=True))
        op.create_index("ix_similarity_audit_logs_external_candidate_id", "similarity_audit_logs", ["external_candidate_id"])

    for table_name in (
        "temu_image_embeddings",
        "temu_similarity_tasks",
        "temu_similarity_clusters",
        "temu_similarity_cluster_members",
        "temu_similarity_candidates",
        "cross_platform_similarity_candidates",
        "cross_platform_product_relations",
        "similarity_scope_preferences",
    ):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_temu_image_embeddings_vector_hnsw "
            "ON temu_image_embeddings USING hnsw (vector vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_temu_image_embeddings_vector_hnsw")

    for table_name in (
        "similarity_scope_preferences",
        "cross_platform_product_relations",
        "cross_platform_similarity_candidates",
        "temu_similarity_candidates",
        "temu_similarity_cluster_members",
        "temu_similarity_clusters",
        "temu_similarity_tasks",
        "temu_image_embeddings",
    ):
        op.drop_table(table_name)

    audit_columns = {column["name"] for column in inspect(bind).get_columns("similarity_audit_logs")}
    if "external_candidate_id" in audit_columns:
        op.drop_index("ix_similarity_audit_logs_external_candidate_id", table_name="similarity_audit_logs")
        op.drop_column("similarity_audit_logs", "external_candidate_id")
    if "candidate_type" in audit_columns:
        op.drop_index("ix_similarity_audit_logs_candidate_type", table_name="similarity_audit_logs")
        op.drop_column("similarity_audit_logs", "candidate_type")

    temu_columns = {column["name"] for column in inspect(bind).get_columns("temu_products")}
    for name in ("similarity_status", "image_phash", "image_content_hash"):
        if name in temu_columns:
            op.drop_index(f"ix_temu_products_{name}", table_name="temu_products")
            op.drop_column("temu_products", name)
    for name in ("image_status", "cached_image_name"):
        if name in temu_columns:
            op.drop_column("temu_products", name)
