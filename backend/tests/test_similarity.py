from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Product, ProductSimilarityClusterMember
from app.similarity_services import (
    VECTOR_STORAGE_DIMENSION,
    build_embedding_request,
    merge_products,
    normalize_vector,
    phash_distance,
)


def product(offer_id: str, pieces: int, daily: str) -> Product:
    return Product(
        offer_id=offer_id,
        product_url=f"https://detail.1688.com/offer/{offer_id}.html",
        listing_date=date(2026, 1, 1),
        listed_days=10,
        total_orders=10,
        total_pieces=pieces,
        calculation_days=10,
        daily_orders=Decimal(daily),
        last_collected_at=datetime.now(timezone.utc),
    )


def test_normalize_vector_pads_fixed_storage_dimension():
    result = normalize_vector([3, 4], 2)
    assert len(result) == VECTOR_STORAGE_DIMENSION
    assert result[:2] == [0.6, 0.8]
    assert result[-1] == 0


def test_phash_distance_accepts_hex_hashes():
    assert phash_distance("0000000000000000", "0000000000000001") == 1


def test_request_template_replaces_image_placeholders():
    class Config:
        model_name = "vision-embedding"
        request_template = {
            "model": "{{model}}",
            "input": [{"image": "{{image_data_url}}", "id": "{{product_id}}"}],
        }

    item = product("123", 10, "1")
    item.cached_image_name = "123.jpg"
    item.original_image_url = "https://example.invalid/123.jpg"
    payload = build_embedding_request(Config(), item, b"image")
    assert payload["model"] == "vision-embedding"
    assert payload["input"][0]["id"] == "123"
    assert payload["input"][0]["image"].startswith("data:image/jpeg;base64,")


def test_merge_products_chooses_total_pieces_representative():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        lower = product("100", 100, "20")
        higher = product("200", 200, "10")
        db.add_all([lower, higher])
        db.flush()
        cluster = merge_products(db, lower.id, higher.id, "manual", 0.95)
        db.commit()
        assert cluster.representative_product_id == higher.id
        assert db.query(ProductSimilarityClusterMember).count() == 2
