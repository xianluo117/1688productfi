from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
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
    image_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    image_phash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    similarity_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
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


class TemuProduct(Base):
    __tablename__ = "temu_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    temu_product_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    yunqi_gdid: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    mall_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    product_url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_image_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_status: Mapped[str] = mapped_column(String(20), default="pending")
    image_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    image_phash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    similarity_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    weekly_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    monthly_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    total_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    weekly_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    monthly_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    review_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shop_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    shop_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    shop_opened_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    shop_total_sales: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    temu_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_ad: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    delivery_tip: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    memberships: Mapped[list[TemuGroupMembership]] = relationship(back_populates="product", cascade="all, delete-orphan")
    snapshots: Mapped[list[TemuProductSnapshot]] = relationship(back_populates="product", cascade="all, delete-orphan")
    favorites: Mapped[list[TemuProductFavorite]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class TemuProductGroup(Base):
    __tablename__ = "temu_product_groups"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_name", name="uq_temu_group_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120), index=True)
    default_sort_field: Mapped[str] = mapped_column(String(40), default="daily_sales")
    default_sort_direction: Mapped[str] = mapped_column(String(4), default="desc")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    memberships: Mapped[list[TemuGroupMembership]] = relationship(back_populates="group", cascade="all, delete-orphan")


class TemuGroupMembership(Base):
    __tablename__ = "temu_group_memberships"
    __table_args__ = (UniqueConstraint("owner_id", "product_id", "group_id", name="uq_temu_owner_product_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("temu_product_groups.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped[TemuProduct] = relationship(back_populates="memberships")
    group: Mapped[TemuProductGroup] = relationship(back_populates="memberships")


class TemuProductFavorite(Base):
    __tablename__ = "temu_product_favorites"
    __table_args__ = (
        UniqueConstraint("owner_id", "product_id", name="uq_temu_favorite_owner_product"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("temu_products.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    product: Mapped[TemuProduct] = relationship(back_populates="favorites")


class TemuCollectionBatch(Base):
    __tablename__ = "temu_collection_batches"
    __table_args__ = (UniqueConstraint("owner_id", "idempotency_key", name="uq_temu_batch_owner_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("temu_product_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    source_page: Mapped[int] = mapped_column(Integer)
    item_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemuProductSnapshot(Base):
    __tablename__ = "temu_product_snapshots"
    __table_args__ = (UniqueConstraint("batch_id", "product_id", name="uq_temu_snapshot_batch_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("temu_collection_batches.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("temu_product_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    weekly_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    monthly_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    total_sales: Mapped[int] = mapped_column(BigInteger, default=0)
    daily_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    weekly_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    monthly_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shop_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    temu_tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_ad: Mapped[bool] = mapped_column(Boolean, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    product: Mapped[TemuProduct] = relationship(back_populates="snapshots")


class ImageEmbeddingConfig(Base):
    __tablename__ = "image_embedding_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str] = mapped_column(Text)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(255))
    output_dimension: Mapped[int] = mapped_column(Integer)
    request_template: Mapped[dict] = mapped_column(JSON)
    response_path: Mapped[str] = mapped_column(String(255), default="embedding.values")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    top_k: Mapped[int] = mapped_column(Integer, default=100)
    phash_auto_distance: Mapped[int] = mapped_column(Integer, default=4)
    review_threshold: Mapped[float] = mapped_column(Float, default=0.86)
    auto_merge_threshold: Mapped[float] = mapped_column(Float, default=0.94)
    auto_merge_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductImageEmbedding(Base):
    __tablename__ = "product_image_embeddings"
    __table_args__ = (
        UniqueConstraint("product_id", "config_id", "image_content_hash", name="uq_product_image_embedding_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    image_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    phash: Mapped[str] = mapped_column(String(16), index=True)
    vector: Mapped[list[float]] = mapped_column(Vector(1536))
    source_dimension: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SimilarityQueueControl(Base):
    __tablename__ = "similarity_queue_control"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    state: Mapped[str] = mapped_column(String(20), default="running")
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    auto_vectorization_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SimilarityTask(Base):
    __tablename__ = "similarity_tasks"
    __table_args__ = (
        UniqueConstraint("product_id", "config_id", "image_content_hash", name="uq_similarity_task_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    image_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductSimilarityCluster(Base):
    __tablename__ = "product_similarity_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    representative_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProductSimilarityClusterMember(Base):
    __tablename__ = "product_similarity_cluster_members"
    __table_args__ = (UniqueConstraint("product_id", name="uq_similarity_cluster_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("product_similarity_clusters.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    decision_source: Mapped[str] = mapped_column(String(20), default="automatic")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductSimilarityCandidate(Base):
    __tablename__ = "product_similarity_candidates"
    __table_args__ = (UniqueConstraint("left_product_id", "right_product_id", "config_id", name="uq_similarity_candidate_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    left_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    right_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    phash_distance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SimilarityAuditLog(Base):
    __tablename__ = "similarity_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("product_similarity_candidates.id", ondelete="SET NULL"), nullable=True)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("product_similarity_clusters.id", ondelete="SET NULL"), nullable=True)
    candidate_type: Mapped[str] = mapped_column(String(30), default="1688_same", index=True)
    external_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TemuImageEmbedding(Base):
    __tablename__ = "temu_image_embeddings"
    __table_args__ = (
        UniqueConstraint("product_id", "config_id", "image_content_hash", name="uq_temu_image_embedding_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    image_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    phash: Mapped[str] = mapped_column(String(16), index=True)
    vector: Mapped[list[float]] = mapped_column(Vector(1536))
    source_dimension: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TemuSimilarityTask(Base):
    __tablename__ = "temu_similarity_tasks"
    __table_args__ = (
        UniqueConstraint("product_id", "config_id", "image_content_hash", name="uq_temu_similarity_task_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    image_content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemuSimilarityCluster(Base):
    __tablename__ = "temu_similarity_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    representative_product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TemuSimilarityClusterMember(Base):
    __tablename__ = "temu_similarity_cluster_members"
    __table_args__ = (UniqueConstraint("product_id", name="uq_temu_similarity_cluster_product"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("temu_similarity_clusters.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    decision_source: Mapped[str] = mapped_column(String(20), default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TemuSimilarityCandidate(Base):
    __tablename__ = "temu_similarity_candidates"
    __table_args__ = (
        UniqueConstraint("left_product_id", "right_product_id", "config_id", name="uq_temu_similarity_candidate_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    left_product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    right_product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    phash_distance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CrossPlatformSimilarityCandidate(Base):
    __tablename__ = "cross_platform_similarity_candidates"
    __table_args__ = (
        UniqueConstraint("product_id", "temu_product_id", "config_id", name="uq_cross_platform_similarity_candidate_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    temu_product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("image_embedding_configs.id", ondelete="CASCADE"), index=True)
    phash_distance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decision_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CrossPlatformProductRelation(Base):
    __tablename__ = "cross_platform_product_relations"
    __table_args__ = (
        UniqueConstraint("product_id", "temu_product_id", name="uq_cross_platform_product_relation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    temu_product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("cross_platform_similarity_candidates.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TemuImageDownloadTask(Base):
    __tablename__ = "temu_image_download_tasks"
    __table_args__ = (
        UniqueConstraint("product_id", "image_url", name="uq_temu_image_download_product_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("temu_products.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("temu_product_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    image_url: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="ingest", index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TemuImageSearchConfig(Base):
    __tablename__ = "temu_image_search_configs"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    base_url: Mapped[str] = mapped_column(Text, default="")
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_image_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_search_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    queue_state: Mapped[str] = mapped_column(String(20), default="running", index=True)
    queue_revision: Mapped[int] = mapped_column(Integer, default=0)
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=2)
    task_timeout_seconds: Mapped[int] = mapped_column(Integer, default=180)
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TemuImageSearchTask(Base):
    __tablename__ = "temu_image_search_tasks"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "image_url", name="uq_temu_image_search_product_url"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("temu_products.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("temu_product_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    image_url: Mapped[str] = mapped_column(Text)
    request_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_image_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="image_ready", index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    remote_stage: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    upload_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    product_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TemuImageSearchResult(Base):
    __tablename__ = "temu_image_search_results"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_temu_image_search_result_task"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("temu_image_search_tasks.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("temu_products.id", ondelete="CASCADE"), index=True
    )
    search_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    found: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class TemuImageSearchResultItem(Base):
    __tablename__ = "temu_image_search_result_items"
    __table_args__ = (
        UniqueConstraint("result_id", "rank", name="uq_temu_image_search_result_rank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(
        ForeignKey("temu_image_search_results.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sale_quantity: Mapped[str | None] = mapped_column(String(120), nullable=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SimilarityScopePreference(Base):
    __tablename__ = "similarity_scope_preferences"

    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    product_group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    temu_group_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
