"""Add image-search queue controls and public cached-image URL support.

Revision ID: 20260812_02
Revises: 20260812_01
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_02"
down_revision: str | None = "20260812_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    config_columns = column_names("temu_image_search_configs")
    config_additions = (
        ("public_image_base_url", sa.Column("public_image_base_url", sa.Text(), nullable=True)),
        (
            "queue_state",
            sa.Column(
                "queue_state",
                sa.String(length=20),
                nullable=False,
                server_default="running",
            ),
        ),
        (
            "queue_revision",
            sa.Column(
                "queue_revision",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        ),
        (
            "concurrency",
            sa.Column(
                "concurrency",
                sa.Integer(),
                nullable=False,
                server_default="4",
            ),
        ),
    )
    for name, column in config_additions:
        if name not in config_columns:
            op.add_column("temu_image_search_configs", column)

    queue_index = "ix_temu_image_search_configs_queue_state"
    if queue_index not in index_names("temu_image_search_configs"):
        op.create_index(
            queue_index,
            "temu_image_search_configs",
            ["queue_state"],
            unique=False,
        )

    task_columns = column_names("temu_image_search_tasks")
    task_additions = (
        ("request_image_url", sa.Column("request_image_url", sa.Text(), nullable=True)),
        (
            "request_image_source",
            sa.Column("request_image_source", sa.String(length=20), nullable=True),
        ),
        (
            "remote_stage",
            sa.Column("remote_stage", sa.String(length=20), nullable=True),
        ),
        (
            "upload_task_id",
            sa.Column("upload_task_id", sa.String(length=64), nullable=True),
        ),
        (
            "product_task_id",
            sa.Column("product_task_id", sa.String(length=64), nullable=True),
        ),
    )
    for name, column in task_additions:
        if name not in task_columns:
            op.add_column("temu_image_search_tasks", column)

    for column_name in ("remote_stage", "upload_task_id", "product_task_id"):
        index_name = f"ix_temu_image_search_tasks_{column_name}"
        if index_name not in index_names("temu_image_search_tasks"):
            op.create_index(
                index_name,
                "temu_image_search_tasks",
                [column_name],
                unique=False,
            )

    if "remote_task_id" in task_columns:
        op.execute(
            sa.text(
                "UPDATE temu_image_search_tasks "
                "SET remote_task_id = NULL, status = 'pending', locked_at = NULL "
                "WHERE remote_task_id IS NOT NULL"
            )
        )


def downgrade() -> None:
    task_columns = column_names("temu_image_search_tasks")
    task_indexes = index_names("temu_image_search_tasks")
    for column_name in ("product_task_id", "upload_task_id", "remote_stage"):
        index_name = f"ix_temu_image_search_tasks_{column_name}"
        if index_name in task_indexes:
            op.drop_index(index_name, table_name="temu_image_search_tasks")
        if column_name in task_columns:
            op.drop_column("temu_image_search_tasks", column_name)
    for column_name in ("request_image_source", "request_image_url"):
        if column_name in task_columns:
            op.drop_column("temu_image_search_tasks", column_name)

    config_columns = column_names("temu_image_search_configs")
    queue_index = "ix_temu_image_search_configs_queue_state"
    if queue_index in index_names("temu_image_search_configs"):
        op.drop_index(queue_index, table_name="temu_image_search_configs")
    for column_name in (
        "concurrency",
        "queue_revision",
        "queue_state",
        "public_image_base_url",
    ):
        if column_name in config_columns:
            op.drop_column("temu_image_search_configs", column_name)
