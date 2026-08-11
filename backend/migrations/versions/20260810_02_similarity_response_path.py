"""Normalize legacy Gemini embedding response paths.

Revision ID: 20260810_02
Revises: 20260810_01
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260810_02"
down_revision: str | None = "20260810_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LEGACY_RESPONSE_PATH = "data.0.embedding"
GEMINI_RESPONSE_PATH = "embedding.values"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE image_embedding_configs "
            "SET response_path = :gemini_path "
            "WHERE response_path = :legacy_path"
        ).bindparams(
            gemini_path=GEMINI_RESPONSE_PATH,
            legacy_path=LEGACY_RESPONSE_PATH,
        )
    )


def downgrade() -> None:
    # Do not restore the invalid legacy path: existing configurations may have
    # been edited intentionally after this migration was applied.
    pass
