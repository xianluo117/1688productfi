from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ProductGroup
from app.services import get_or_create_group


def create_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_or_create_group_creates_cleaned_name():
    with create_test_session() as db:
        group, created = get_or_create_group(db, "  夏季   女装 ")

        assert created is True
        assert group.name == "夏季 女装"
        assert group.normalized_name == "夏季 女装"


def test_get_or_create_group_reuses_normalized_name():
    with create_test_session() as db:
        first, first_created = get_or_create_group(db, "Summer Deals")
        second, second_created = get_or_create_group(db, "  SUMMER   deals ")

        assert first_created is True
        assert second_created is False
        assert second.id == first.id
        assert db.query(ProductGroup).count() == 1
