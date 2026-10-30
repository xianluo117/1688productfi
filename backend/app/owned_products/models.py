from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class RecordTimes:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OwnedBatch(RecordTimes, Base):
    __tablename__ = "owned_product_batches"

    dify_batch_number: Mapped[str | None] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(Text)
    autoimage_batch_number: Mapped[str | None] = mapped_column(Text)
    autoexcel_batch_number: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_stage: Mapped[str | None] = mapped_column(Text)
    final_run_result: Mapped[str | None] = mapped_column(Text)
    original_item_code_summary: Mapped[str | None] = mapped_column(Text)
    spreadsheet_url_1: Mapped[str | None] = mapped_column(Text)
    spreadsheet_url_2: Mapped[str | None] = mapped_column(Text)
    temu_upload_result: Mapped[str | None] = mapped_column(Text)
    exception_info: Mapped[str | None] = mapped_column(Text)


class OwnedItem(RecordTimes, Base):
    __tablename__ = "owned_product_items"
    __table_args__ = (
        Index("ix_owned_product_items_batch_id_id", "batch_id", "id"),
        Index("ix_owned_product_items_new_item_code", "new_item_code"),
    )

    batch_id: Mapped[int] = mapped_column(ForeignKey("owned_product_batches.id"))
    original_item_code: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    new_item_code: Mapped[str | None] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(Text)
    original_specifications: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    processed_specifications: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    original_sizes: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    processed_sizes: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True))
    is_discarded: Mapped[bool | None] = mapped_column(Boolean)
    discard_reason: Mapped[str | None] = mapped_column(Text)
    candidate_image_download_result: Mapped[str | None] = mapped_column(Text)
    candidate_image_download_exception: Mapped[str | None] = mapped_column(Text)
    candidate_generation_result: Mapped[str | None] = mapped_column(Text)
    candidate_generation_exception: Mapped[str | None] = mapped_column(Text)


class OwnedColor(RecordTimes, Base):
    __tablename__ = "owned_product_colors"
    __table_args__ = (Index("ix_owned_product_colors_item_id_id", "item_id", "id"),)

    item_id: Mapped[int] = mapped_column(ForeignKey("owned_product_items.id"))
    original_color: Mapped[str | None] = mapped_column(Text)
    spreadsheet_standard_color: Mapped[str | None] = mapped_column(Text)
    run_result: Mapped[str | None] = mapped_column(Text)
    main_image_visual_result: Mapped[str | None] = mapped_column(Text)
    image_generation_result: Mapped[str | None] = mapped_column(Text)
    image_urls: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB(none_as_null=True))
