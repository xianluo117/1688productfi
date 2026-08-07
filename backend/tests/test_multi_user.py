from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.auth import hash_ingest_token, issue_ingest_token
from app.database import Base
from app.models import AdminUser, Product, ProductGroup, ProductGroupMembership
from app.services import ProductFilters, list_group_products


def create_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def create_user(db: Session, username: str) -> AdminUser:
    user = AdminUser(username=username, password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_product(db: Session, offer_id: str, group: ProductGroup, owner: AdminUser, listed_days: int, daily: str) -> Product:
    product = Product(
        offer_id=offer_id,
        product_url=f"https://detail.1688.com/offer/{offer_id}.html",
        listing_date=date(2026, 1, 1),
        listed_days=listed_days,
        total_orders=100,
        total_pieces=500,
        calculation_days=listed_days,
        daily_orders=Decimal(daily),
        average_pieces_per_order=Decimal("5"),
        last_collected_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    db.add(product)
    db.flush()
    db.add(ProductGroupMembership(owner_id=owner.id, product_id=product.id, group_id=group.id))
    db.commit()
    return product


def test_ingest_token_is_stored_as_hash():
    with create_session() as db:
        user = create_user(db, "token-user")
        token = issue_ingest_token(user)
        db.commit()

        assert token.startswith("zoo_")
        assert user.ingest_token_hash == hash_ingest_token(token)
        assert token != user.ingest_token_hash
        assert user.ingest_token_prefix == token[:12]


def test_product_filters_are_combined_with_owner_membership():
    with create_session() as db:
        first_owner = create_user(db, "first")
        second_owner = create_user(db, "second")
        group = ProductGroup(owner_id=first_owner.id, name="A", normalized_name="a")
        other_group = ProductGroup(owner_id=second_owner.id, name="A", normalized_name="a")
        db.add_all([group, other_group])
        db.commit()
        create_product(db, "1001", group, first_owner, 10, "10")
        create_product(db, "1002", group, first_owner, 500, "1")
        create_product(db, "1003", other_group, second_owner, 20, "20")

        products, total = list_group_products(
            db,
            group.id,
            first_owner.id,
            1,
            30,
            "daily_orders",
            "desc",
            ProductFilters(listed_days_max=100, daily_orders_min=Decimal("5")),
        )

        assert total == 1
        assert [product.offer_id for product in products] == ["1001"]
