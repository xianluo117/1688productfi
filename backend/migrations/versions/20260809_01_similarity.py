"""Add image similarity configuration, vectors, tasks, and global clusters.

Revision ID: 20260809_01
Revises: 20260808_01
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app import models  # noqa: F401
from app.database import Base

revision: str = "20260809_01"
down_revision: str | None = "20260808_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    existing_columns = {column["name"] for column in inspect(bind).get_columns("products")}
    if "image_content_hash" not in existing_columns:
        op.add_column("products", sa.Column("image_content_hash", sa.String(length=64), nullable=True))
        op.create_index("ix_products_image_content_hash", "products", ["image_content_hash"])
    if "image_phash" not in existing_columns:
        op.add_column("products", sa.Column("image_phash", sa.String(length=16), nullable=True))
        op.create_index("ix_products_image_phash", "products", ["image_phash"])
    if "similarity_status" not in existing_columns:
        op.add_column(
            "products",
            sa.Column("similarity_status", sa.String(length=20), nullable=False, server_default="pending"),
        )
        op.create_index("ix_products_similarity_status", "products", ["similarity_status"])

    for table_name in (
        "image_embedding_configs",
        "product_image_embeddings",
        "similarity_tasks",
        "product_similarity_clusters",
        "product_similarity_cluster_members",
        "product_similarity_candidates",
        "similarity_audit_logs",
    ):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_product_image_embeddings_vector_hnsw "
            "ON product_image_embeddings USING hnsw (vector vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_product_image_embeddings_vector_hnsw")

    for table_name in (
        "similarity_audit_logs",
        "product_similarity_candidates",
        "product_similarity_cluster_members",
        "product_similarity_clusters",
        "similarity_tasks",
        "product_image_embeddings",
        "image_embedding_configs",
    ):
        op.drop_table(table_name)

    existing_columns = {column["name"] for column in inspect(bind).get_columns("products")}
    for column_name in (
        "similarity_status",
        "image_phash",
        "image_content_hash",
    ):
        if column_name in existing_columns:
            op.drop_index(f"ix_products_{column_name}", table_name="products")
            op.drop_column("products", column_name)
