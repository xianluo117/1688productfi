"""Add persistent Temu to 1688 image-search queue and results.

Revision ID: 20260812_01
Revises: 20260811_02
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

from app.database import Base
import app.models  # noqa: F401

revision: str = "20260812_01"
down_revision: str | None = "20260811_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAMES = (
    "temu_image_search_configs",
    "temu_image_search_tasks",
    "temu_image_search_results",
    "temu_image_search_result_items",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLE_NAMES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(TABLE_NAMES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
