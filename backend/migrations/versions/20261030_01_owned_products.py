"""Add independent owned product batches, items and colors.

Revision ID: 20261030_01
Revises: 20260813_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261030_01"
down_revision = "20260813_01"
branch_labels = None
depends_on = None


def record_columns():
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    # The initial migration creates current metadata on an empty database.
    if not op.get_context().as_sql and {
        "owned_product_batches", "owned_product_items", "owned_product_colors"
    }.issubset(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "owned_product_batches",
        *record_columns(),
        sa.Column("dify_batch_number", sa.Text()),
        sa.Column("username", sa.Text()),
        sa.Column("autoimage_batch_number", sa.Text()),
        sa.Column("autoexcel_batch_number", sa.Text()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("run_started_at", sa.DateTime(timezone=True)),
        sa.Column("processing_stage", sa.Text()),
        sa.Column("final_run_result", sa.Text()),
        sa.Column("original_item_code_summary", sa.Text()),
        sa.Column("spreadsheet_url_1", sa.Text()),
        sa.Column("spreadsheet_url_2", sa.Text()),
        sa.Column("temu_upload_result", sa.Text()),
        sa.Column("exception_info", sa.Text()),
    )
    op.create_table(
        "owned_product_items",
        *record_columns(),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("owned_product_batches.id"), nullable=False),
        sa.Column("original_item_code", sa.Text()),
        sa.Column("source_url", sa.Text()),
        sa.Column("new_item_code", sa.Text()),
        sa.Column("username", sa.Text()),
        sa.Column("original_specifications", postgresql.JSONB(none_as_null=True)),
        sa.Column("processed_specifications", postgresql.JSONB(none_as_null=True)),
        sa.Column("original_sizes", postgresql.JSONB(none_as_null=True)),
        sa.Column("processed_sizes", postgresql.JSONB(none_as_null=True)),
        sa.Column("is_discarded", sa.Boolean()),
        sa.Column("discard_reason", sa.Text()),
        sa.Column("candidate_image_download_result", sa.Text()),
        sa.Column("candidate_image_download_exception", sa.Text()),
        sa.Column("candidate_generation_result", sa.Text()),
        sa.Column("candidate_generation_exception", sa.Text()),
    )
    op.create_index("ix_owned_product_items_batch_id_id", "owned_product_items", ["batch_id", "id"])
    op.create_index("ix_owned_product_items_new_item_code", "owned_product_items", ["new_item_code"])
    op.create_table(
        "owned_product_colors",
        *record_columns(),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("owned_product_items.id"), nullable=False),
        sa.Column("original_color", sa.Text()),
        sa.Column("spreadsheet_standard_color", sa.Text()),
        sa.Column("run_result", sa.Text()),
        sa.Column("main_image_visual_result", sa.Text()),
        sa.Column("image_generation_result", sa.Text()),
        sa.Column("image_urls", postgresql.JSONB(none_as_null=True)),
    )
    op.create_index("ix_owned_product_colors_item_id_id", "owned_product_colors", ["item_id", "id"])


def downgrade() -> None:
    op.drop_table("owned_product_colors")
    op.drop_table("owned_product_items")
    op.drop_table("owned_product_batches")
