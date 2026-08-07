from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ingest_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    ingest_token_prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ingest_token_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    groups: Mapped[list[ProductGroup]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class ProductGroup(Base):
    __tablename__ = "product_groups"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_name", name="uq_product_group_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120), index=True)
    default_sort_field: Mapped[str] = mapped_column(String(40), default="daily_orders")
    default_sort_direction: Mapped[str] = mapped_column(String(4), default="desc")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    owner: Mapped[AdminUser] = relationship(back_populates="groups")
    memberships: Mapped[list[ProductGroupMembership]] = relationship(back_populates="group", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    product_url: Mapped[str] = mapped_column(Text)
    original_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_image_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_status: Mapped[str] = mapped_column(String(20), default="pending")
    listing_date: Mapped[date] = mapped_column(Date)
    listed_days: Mapped[int] = mapped_column(Integer)
    total_orders: Mapped[int] = mapped_column(BigInteger)
    total_pieces: Mapped[int] = mapped_column(BigInteger)
    calculation_days: Mapped[int] = mapped_column(Integer)
    daily_orders: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    average_pieces_per_order: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    first_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    memberships: Mapped[list[ProductGroupMembership]] = relationship(back_populates="product", cascade="all, delete-orphan")
    snapshots: Mapped[list[ProductSnapshot]] = relationship(back_populates="product", cascade="all, delete-orphan")


class ProductGroupMembership(Base):
    __tablename__ = "product_group_memberships"
    __table_args__ = (
        UniqueConstraint("product_id", "group_id", name="uq_product_group_membership"),
        UniqueConstraint("owner_id", "product_id", "group_id", name="uq_owner_product_group_membership"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("product_groups.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="memberships")
    group: Mapped[ProductGroup] = relationship(back_populates="memberships")


class CollectionBatch(Base):
    __tablename__ = "collection_batches"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_collection_batch_owner_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("product_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    source_page: Mapped[int] = mapped_column(Integer)
    item_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"
    __table_args__ = (UniqueConstraint("batch_id", "product_id", name="uq_snapshot_batch_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("collection_batches.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("product_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    listing_date: Mapped[date] = mapped_column(Date)
    listed_days: Mapped[int] = mapped_column(Integer)
    total_orders: Mapped[int] = mapped_column(BigInteger)
    total_pieces: Mapped[int] = mapped_column(BigInteger)
    calculation_days: Mapped[int] = mapped_column(Integer)
    daily_orders: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    average_pieces_per_order: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    product: Mapped[Product] = relationship(back_populates="snapshots")
