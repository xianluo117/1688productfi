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


class LoginRequest(BaseModel):
    username: str
    password: str


class AdminResponse(BaseModel):
    username: str


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


class ProductPage(BaseModel):
    items: list[ProductListItem]
    total: int
    page: int
    page_size: int
    sort_field: str
    sort_direction: str


class SnapshotResponse(BaseModel):
    id: int
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
