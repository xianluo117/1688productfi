from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


SortField = Literal[
    "offer_id",
    "listing_date",
    "listed_days",
    "total_orders",
    "total_pieces",
    "daily_orders",
    "last_collected_at",
]
SortDirection = Literal["asc", "desc"]
TemuSortField = Literal[
    "temu_product_id",
    "sale_price",
    "original_price",
    "daily_sales",
    "weekly_sales",
    "total_sales",
    "listing_date",
    "last_collected_at",
]
UserRole = Literal["admin", "user"]


class LoginRequest(BaseModel):
    username: str
    password: str


class CurrentUserResponse(BaseModel):
    id: int
    username: str
    role: UserRole
    is_active: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=200)
    role: UserRole = "user"


class UserStatusUpdate(BaseModel):
    is_active: bool


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class UserResponse(CurrentUserResponse):
    model_config = ConfigDict(from_attributes=True)

    ingest_token_prefix: str | None
    ingest_token_updated_at: datetime | None
    created_at: datetime


class UserCreatedResponse(UserResponse):
    ingest_token: str


class TokenResetResponse(BaseModel):
    ingest_token: str
    ingest_token_prefix: str
    ingest_token_updated_at: datetime


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("group name cannot be empty")
        return cleaned


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_sort_field: SortField | None = None
    default_sort_direction: SortDirection | None = None

    @field_validator("name")
    @classmethod
    def clean_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("group name cannot be empty")
        return cleaned


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    name: str
    default_sort_field: str
    default_sort_direction: str
    created_at: datetime


class IngestProduct(BaseModel):
    offer_id: str = Field(min_length=1, max_length=40, pattern=r"^\d+$")
    product_url: HttpUrl
    image_url: HttpUrl | None = None
    listing_date: date
    listed_days: int = Field(ge=0, le=100000)
    total_orders: int = Field(ge=0)
    total_pieces: int = Field(ge=0)


class IngestBatchRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    group_id: int = Field(gt=0)
    source_page: int = Field(gt=0)
    products: list[IngestProduct]

    @model_validator(mode="after")
    def reject_duplicate_products(self) -> "IngestBatchRequest":
        offer_ids = [product.offer_id for product in self.products]
        if len(offer_ids) != len(set(offer_ids)):
            raise ValueError("products must not contain duplicate offer IDs")
        return self


class ImageEmbeddingConfigUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: HttpUrl
    api_key: str | None = Field(default=None, max_length=4000)
    model_name: str = Field(min_length=1, max_length=255)
    output_dimension: int = Field(gt=0, le=1536)
    request_template: dict
    response_path: str = Field(default="data.0.embedding", min_length=1, max_length=255)
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    top_k: int = Field(default=100, ge=1, le=500)
    phash_auto_distance: int = Field(default=4, ge=0, le=64)
    review_threshold: float = Field(default=0.86, ge=-1, le=1)
    auto_merge_threshold: float = Field(default=0.94, ge=-1, le=1)
    auto_merge_enabled: bool = False
    activate: bool = True

    @model_validator(mode="after")
    def validate_thresholds(self) -> "ImageEmbeddingConfigUpsert":
        if self.auto_merge_threshold < self.review_threshold:
            raise ValueError("auto merge threshold must be at least review threshold")
        return self


class ImageEmbeddingConfigResponse(BaseModel):
    id: int
    name: str
    base_url: str
    api_key_masked: str | None
    model_name: str
    output_dimension: int
    request_template: dict
    response_path: str
    timeout_seconds: int
    top_k: int
    phash_auto_distance: int
    review_threshold: float
    auto_merge_threshold: float
    auto_merge_enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class IngestBatchResponse(BaseModel):
    batch_id: int
    accepted: int
    duplicate: bool = False


class ProductListItem(BaseModel):
    id: int
    offer_id: str
    product_url: str
    image_url: str | None
    listing_date: date
    listed_days: int
    total_orders: int
    total_pieces: int
    calculation_days: int
    daily_orders: Decimal
    average_pieces_per_order: Decimal | None
    last_collected_at: datetime
    cluster_id: int | None = None
    cluster_member_count: int = 1


class SimilaritySourceResponse(BaseModel):
    user_id: int
    username: str
    group_id: int
    group_name: str


class SimilarityProductResponse(ProductListItem):
    similarity_status: str
    sources: list[SimilaritySourceResponse] = Field(default_factory=list)


class SimilarityCandidateResponse(BaseModel):
    id: int
    left: SimilarityProductResponse
    right: SimilarityProductResponse
    phash_distance: int | None
    image_similarity: float | None
    status: str
    created_at: datetime


class SimilarityDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


class SimilarityClusterResponse(BaseModel):
    id: int
    representative: SimilarityProductResponse
    members: list[SimilarityProductResponse]


class SimilarityQueueUpdate(BaseModel):
    concurrency: int = Field(ge=1, le=32)


class SimilarityTaskResponse(BaseModel):
    id: int
    product_id: int
    offer_id: str
    config_id: int
    config_name: str
    status: str
    attempts: int
    last_error: str | None
    available_at: datetime
    locked_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class SimilarityConnectionTestResponse(BaseModel):
    success: bool
    product_id: int
    offer_id: str
    endpoint: str
    returned_dimension: int
    elapsed_ms: int
    message: str


class SimilarityTaskStatsResponse(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    review_pending: int = 0
    completed_last_minute: int = 0
    queue_state: Literal["running", "paused", "stopped"] = "running"
    concurrency: int = 4


class ProductPage(BaseModel):
    items: list[ProductListItem]
    total: int
    page: int
    page_size: int
    sort_field: str
    sort_direction: str


class SnapshotResponse(BaseModel):
    id: int
    owner_id: int
    group_id: int | None
    listing_date: date
    listed_days: int
    total_orders: int
    total_pieces: int
    calculation_days: int
    daily_orders: Decimal
    average_pieces_per_order: Decimal | None
    collected_at: datetime


class ProductDetail(ProductListItem):
    original_image_url: str | None
    image_status: str
    groups: list[GroupResponse]
    snapshots: list[SnapshotResponse]


class TemuGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_sort_field: TemuSortField | None = None
    default_sort_direction: SortDirection | None = None

    @field_validator("name")
    @classmethod
    def clean_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("group name cannot be empty")
        return cleaned


class TemuGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    name: str
    default_sort_field: str
    default_sort_direction: str
    created_at: datetime


class TemuIngestProduct(BaseModel):
    temu_product_id: str = Field(min_length=1, max_length=40, pattern=r"^\d+$")
    yunqi_gdid: str | None = Field(default=None, max_length=40, pattern=r"^\d+$")
    mall_id: str | None = Field(default=None, max_length=40)
    title: str = Field(min_length=1, max_length=2000)
    product_url: HttpUrl
    image_url: HttpUrl | None = None
    sale_price: Decimal | None = Field(default=None, ge=0)
    original_price: Decimal | None = Field(default=None, ge=0)
    daily_sales: int = Field(default=0, ge=0)
    weekly_sales: int = Field(default=0, ge=0)
    monthly_sales: int = Field(default=0, ge=0)
    total_sales: int = Field(default=0, ge=0)
    daily_revenue: Decimal | None = Field(default=None, ge=0)
    weekly_revenue: Decimal | None = Field(default=None, ge=0)
    monthly_revenue: Decimal | None = Field(default=None, ge=0)
    total_revenue: Decimal | None = Field(default=None, ge=0)
    listing_date: date | None = None
    category: str | None = Field(default=None, max_length=255)
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    shop_name: str | None = Field(default=None, max_length=255)
    shop_type: str | None = Field(default=None, max_length=80)
    shop_opened_at: date | None = None
    shop_total_sales: int | None = Field(default=None, ge=0)
    temu_tags: str | None = None
    is_ad: bool = False
    delivery_tip: str | None = None
    association_status: Literal["商品ID匹配", "容器关联"]


class TemuIngestBatchRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    group_id: int = Field(gt=0)
    source_page: int = Field(gt=0)
    products: list[TemuIngestProduct]


class TemuProductListItem(BaseModel):
    id: int
    temu_product_id: str
    yunqi_gdid: str | None
    mall_id: str | None
    title: str
    product_url: str
    image_url: str | None
    sale_price: Decimal | None
    original_price: Decimal | None
    daily_sales: int
    weekly_sales: int
    monthly_sales: int
    total_sales: int
    daily_revenue: Decimal | None
    weekly_revenue: Decimal | None
    monthly_revenue: Decimal | None
    total_revenue: Decimal | None
    listing_date: date | None
    listed_days: int | None
    category: str | None
    rating: Decimal | None
    review_count: int | None
    shop_name: str | None
    shop_type: str | None
    derived_shop_type: str | None
    shop_opened_at: date | None
    shop_total_sales: int | None
    temu_tags: str | None
    is_ad: bool
    delivery_tip: str | None
    last_collected_at: datetime


class TemuProductPage(BaseModel):
    items: list[TemuProductListItem]
    total: int
    page: int
    page_size: int
    sort_field: str
    sort_direction: str


class TemuSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    group_id: int | None
    sale_price: Decimal | None
    original_price: Decimal | None
    daily_sales: int
    weekly_sales: int
    monthly_sales: int
    total_sales: int
    daily_revenue: Decimal | None
    weekly_revenue: Decimal | None
    monthly_revenue: Decimal | None
    total_revenue: Decimal | None
    listing_date: date | None
    shop_type: str | None
    temu_tags: str | None
    is_ad: bool
    collected_at: datetime


class TemuProductDetail(TemuProductListItem):
    groups: list[TemuGroupResponse]
    snapshots: list[TemuSnapshotResponse]
