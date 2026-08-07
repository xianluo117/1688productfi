from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ProductGroup
from app.services import get_or_create_group


def create_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def create_owner(db: Session, username: str) -> int:
    from app.models import AdminUser

    user = AdminUser(
        username=username,
        password_hash="test-hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user.id


def test_get_or_create_group_creates_cleaned_name():
    with create_test_session() as db:
        owner_id = create_owner(db, "owner-a")
        group, created = get_or_create_group(db, owner_id, "  夏季   女装 ")

        assert created is True
        assert group.owner_id == owner_id
        assert group.name == "夏季 女装"
        assert group.normalized_name == "夏季 女装"


def test_get_or_create_group_reuses_normalized_name_for_same_owner():
    with create_test_session() as db:
        owner_id = create_owner(db, "owner-a")
        first, first_created = get_or_create_group(db, owner_id, "Summer Deals")
        second, second_created = get_or_create_group(db, owner_id, "  SUMMER   deals ")

        assert first_created is True
        assert second_created is False
        assert second.id == first.id
        assert db.query(ProductGroup).count() == 1


def test_same_group_name_is_allowed_for_different_owners():
    with create_test_session() as db:
        first_owner_id = create_owner(db, "owner-a")
        second_owner_id = create_owner(db, "owner-b")
        first, _ = get_or_create_group(db, first_owner_id, "Summer Deals")
        second, _ = get_or_create_group(db, second_owner_id, "SUMMER deals")

        assert first.id != second.id
        assert db.query(ProductGroup).count() == 2
