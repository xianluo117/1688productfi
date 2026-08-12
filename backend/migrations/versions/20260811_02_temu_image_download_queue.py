"""Add persistent Temu image download queue.

Revision ID: 20260811_02
Revises: 20260811_01
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

from app import models  # noqa: F401
from app.database import Base

revision: str = "20260811_02"
down_revision: str | None = "20260811_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["temu_image_download_tasks"].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    op.drop_table("temu_image_download_tasks")
