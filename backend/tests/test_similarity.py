from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    AdminUser,
    ImageEmbeddingConfig,
    Product,
    ProductGroup,
    ProductGroupMembership,
    ProductSimilarityClusterMember,
    SimilarityTask,
)
from app.similarity_services import (
    VECTOR_STORAGE_DIMENSION,
    build_embedding_request,
    embedding_endpoint_url,
    merge_products,
    normalize_vector,
    phash_distance,
    prepare_embedding_image,
    queue_control,
    reset_embedding_scope,
    scope_product_ids,
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


def test_normalize_vector_truncates_larger_matryoshka_response():
    result = normalize_vector([3, 4, 12], 2)
    assert len(result) == VECTOR_STORAGE_DIMENSION
    assert result[:2] == [0.6, 0.8]
    assert result[2:] == [0.0] * (VECTOR_STORAGE_DIMENSION - 2)


def test_phash_distance_accepts_hex_hashes():
    assert phash_distance("0000000000000000", "0000000000000001") == 1


def test_embedding_endpoint_accepts_gemini_base_or_full_url():
    model = "gemini-embedding-2"
    assert embedding_endpoint_url("https://generativelanguage.googleapis.com/v1beta", model) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent"
    )
    assert embedding_endpoint_url("https://proxy.example.com", model) == (
        "https://proxy.example.com/v1beta/models/gemini-embedding-2:embedContent"
    )
    assert embedding_endpoint_url("https://proxy.example.com/v1beta/models", model) == (
        "https://proxy.example.com/v1beta/models/gemini-embedding-2:embedContent"
    )
    assert embedding_endpoint_url(
        "https://proxy.example.com/v1beta/models/gemini-embedding-2:embedContent",
        model,
    ) == "https://proxy.example.com/v1beta/models/gemini-embedding-2:embedContent"


def test_prepare_embedding_image_converts_transparent_png_to_jpeg():
    source = BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 100)).save(source, format="PNG")
    result = prepare_embedding_image(source.getvalue())
    assert result.startswith(b"\xff\xd8")
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_queue_control_defaults_to_running_with_four_workers():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        control = queue_control(db)
        assert control.state == "running"
        assert control.concurrency == 4
        assert control.auto_vectorization_enabled is True


def test_request_template_replaces_gemini_image_placeholders():
    class Config:
        model_name = "gemini-embedding-2"
        output_dimension = 1536
        request_template = {
            "content": {
                "parts": [{"inline_data": {"mime_type": "image/jpeg", "data": "{{image_base64}}"}}]
            },
            "output_dimensionality": "{{output_dimension}}",
        }

    item = product("123", 10, "1")
    item.cached_image_name = "123.jpg"
    payload = build_embedding_request(Config(), item, b"image")
    assert payload["content"]["parts"][0]["inline_data"]["data"] == "aW1hZ2U="
    assert payload["output_dimensionality"] == 1536


def test_scope_product_ids_deduplicates_products_across_groups():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = AdminUser(username="owner", password_hash="hash", role="user")
        item = product("300", 10, "1")
        left = ProductGroup(owner=owner, name="A", normalized_name="a")
        right = ProductGroup(owner=owner, name="B", normalized_name="b")
        db.add_all([owner, item, left, right])
        db.flush()
        db.add_all([
            ProductGroupMembership(owner_id=owner.id, product_id=item.id, group_id=left.id),
            ProductGroupMembership(owner_id=owner.id, product_id=item.id, group_id=right.id),
        ])
        db.flush()
        assert scope_product_ids(db, owner.id, [left.id, right.id]) == [item.id]


def test_scope_product_ids_rejects_empty_scope():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        try:
            scope_product_ids(db, 1, [])
        except ValueError as exc:
            assert "至少选择一个" in str(exc)
        else:
            raise AssertionError("empty scope must be rejected")


def test_reset_embedding_scope_preserves_unselected_tasks():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        config = ImageEmbeddingConfig(
            name="test",
            base_url="https://example.com/v1beta",
            model_name="gemini-embedding-2",
            output_dimension=1536,
            request_template={},
            response_path="embedding.values",
        )
        selected = product("400", 10, "1")
        selected.cached_image_name = "400.jpg"
        selected.image_status = "ready"
        untouched = product("500", 10, "1")
        untouched.cached_image_name = "500.jpg"
        untouched.image_status = "ready"
        db.add_all([config, selected, untouched])
        db.flush()
        db.add_all([
            SimilarityTask(product_id=selected.id, config_id=config.id, image_content_hash="old-selected"),
            SimilarityTask(product_id=untouched.id, config_id=config.id, image_content_hash="old-untouched"),
        ])
        db.flush()
        reset_embedding_scope(db, config, [selected.id])
        db.flush()
        tasks = list(db.query(SimilarityTask).order_by(SimilarityTask.product_id))
        assert len(tasks) == 2
        assert tasks[0].product_id == selected.id
        assert tasks[0].image_content_hash == "pending:400.jpg"
        assert tasks[1].product_id == untouched.id
        assert tasks[1].image_content_hash == "old-untouched"


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
