from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.schemas import SimilarityBatchDecisionRequest
from app.models import (
    AdminUser,
    ImageEmbeddingConfig,
    Product,
    ProductGroup,
    ProductGroupMembership,
    ProductSimilarityCandidate,
    ProductSimilarityClusterMember,
    SimilarityAuditLog,
    SimilarityTask,
    TemuProduct,
    TemuSimilarityTask,
)
from app.similarity_router import apply_candidate_decision
from app.similarity_services import (
    VECTOR_STORAGE_DIMENSION,
    build_embedding_request,
    embedding_endpoint_url,
    embedding_request_headers,
    enqueue_scope_products,
    enqueue_scope_temu_products,
    extract_embedding_values,
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


def test_embedding_config_trims_api_key_whitespace():
    from app.schemas import ImageEmbeddingConfigUpsert

    payload = ImageEmbeddingConfigUpsert(
        name="test",
        base_url="https://example.com",
        api_key="  secret-token\r\n",
        model_name="gemini-embedding-2",
        output_dimension=1536,
        response_path="embedding.values",
        activate=False,
    )
    assert payload.api_key == "secret-token"


def test_batch_decision_request_deduplicates_candidate_ids():
    payload = SimilarityBatchDecisionRequest(
        candidate_ids=["1688_same:3", "temu_same:1", "1688_same:3", "cross_platform:2"],
        decision="approve",
    )
    assert payload.candidate_ids == ["1688_same:3", "temu_same:1", "cross_platform:2"]
    assert payload.decision == "approve"


def test_apply_candidate_reject_creates_manual_audit_record():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        admin = AdminUser(username="admin", password_hash="hash", role="admin")
        left = product("601", 10, "1")
        right = product("602", 20, "2")
        config = ImageEmbeddingConfig(
            name="test",
            base_url="https://example.com/v1beta",
            model_name="gemini-embedding-2",
            output_dimension=1536,
            request_template={},
            response_path="embedding.values",
        )
        db.add_all([admin, left, right, config])
        db.flush()
        candidate = ProductSimilarityCandidate(
            left_product_id=left.id,
            right_product_id=right.id,
            config_id=config.id,
            status="pending",
        )
        db.add(candidate)
        db.flush()
        apply_candidate_decision(db, candidate, "reject", admin.id)
        db.flush()
        audit = db.query(SimilarityAuditLog).one()
        assert candidate.status == "rejected"
        assert candidate.reviewed_by == admin.id
        assert audit.action == "reject"
        assert audit.candidate_id == candidate.id


def test_legacy_openai_response_path_falls_back_to_gemini_values():
    payload = {"embedding": {"values": [3, 4]}}
    assert extract_embedding_values(payload, "data.0.embedding") == [3, 4]


def test_configured_custom_response_path_remains_strict():
    payload = {"embedding": {"values": [3, 4]}}
    try:
        extract_embedding_values(payload, "custom.vector")
    except KeyError as error:
        assert error.args == ("custom",)
    else:
        raise AssertionError("custom response path unexpectedly used Gemini fallback")


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


def test_embedding_request_headers_support_official_and_proxy_auth():
    headers = embedding_request_headers("secret-token")
    assert headers["x-goog-api-key"] == "secret-token"
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Content-Type"] == "application/json"


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


def test_embedding_request_uses_managed_gemini_image_format():
    class Config:
        model_name = "gemini-embedding-2"
        output_dimension = 1536
        request_template = {"content": {"parts": []}}

    item = product("123", 10, "1")
    item.cached_image_name = "123.jpg"
    payload = build_embedding_request(Config(), item, b"image")
    assert payload["content"]["parts"][0]["inline_data"]["data"] == "aW1hZ2U="
    assert payload["content"]["parts"][0]["inline_data"]["mime_type"] == "image/jpeg"
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


def test_scope_product_ids_empty_scope_returns_all_products():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = product("701", 10, "1")
        second = product("702", 20, "2")
        db.add_all([first, second])
        db.flush()
        assert scope_product_ids(db, None, []) == [first.id, second.id]


def test_enqueue_scope_products_skips_duplicate_current_task():
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
        item = product("703", 10, "1")
        item.cached_image_name = "703.jpg"
        item.image_status = "ready"
        db.add_all([config, item])
        db.flush()
        assert enqueue_scope_products(db, config, [item.id]) == 1
        db.flush()
        assert enqueue_scope_products(db, config, [item.id]) == 1
        assert db.query(SimilarityTask).count() == 1


def test_enqueue_scope_temu_products_accepts_remote_image_without_cache():
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
        item = TemuProduct(
            temu_product_id="9001",
            title="Temu remote image",
            product_url="https://www.temu.com/g-9001.html",
            image_url="https://img.kwcdn.com/product/fancy/remote.jpg",
            image_status="pending",
        )
        db.add_all([config, item])
        db.flush()

        assert enqueue_scope_temu_products(db, config, [item.id]) == 1
        assert db.query(TemuSimilarityTask).count() == 1


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
