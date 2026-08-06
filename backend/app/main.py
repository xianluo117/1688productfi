from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    bootstrap_admin,
    create_session_token,
    get_current_admin,
    require_ingest_token,
    verify_password,
)
from .config import Settings, get_settings
from .database import Base, SessionLocal, engine, get_db
from .image_cache import cache_product_image
from .models import AdminUser, Product, ProductGroup, ProductGroupMembership
from .schemas import (
    AdminResponse,
    GroupCreate,
    GroupResponse,
    GroupUpdate,
    IngestBatchRequest,
    IngestBatchResponse,
    LoginRequest,
    ProductDetail,
    ProductPage,
    SortDirection,
    SortField,
)
from .services import (
    get_product_with_relations,
    ingest_batch,
    list_group_products,
    normalize_group_name,
    product_to_item,
    snapshot_to_response,
)


logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.image_storage_path.mkdir(parents=True, exist_ok=True)
    for attempt in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            with SessionLocal() as db:
                bootstrap_admin(db, settings)
            break
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(2)
    yield


app = FastAPI(title="1688 Product Manager", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/images", StaticFiles(directory=settings.image_storage_path, check_dir=False), name="images")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=AdminResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    app_settings: Annotated[Settings, Depends(get_settings)],
) -> AdminResponse:
    user = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    response.set_cookie(
        "admin_session",
        create_session_token(user, app_settings),
        max_age=app_settings.session_max_age,
        httponly=True,
        secure=app_settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return AdminResponse(username=user.username)


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response.delete_cookie("admin_session", path="/")
    return response


@app.get("/api/auth/me", response_model=AdminResponse)
def me(admin: Annotated[AdminUser, Depends(get_current_admin)]) -> AdminResponse:
    return AdminResponse(username=admin.username)


def get_group_or_404(db: Session, group_id: int) -> ProductGroup:
    group = db.get(ProductGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    return group


@app.get("/api/groups", response_model=list[GroupResponse])
def list_groups(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(get_current_admin)],
) -> list[ProductGroup]:
    return list(db.scalars(select(ProductGroup).order_by(ProductGroup.name.asc())))


@app.post("/api/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(get_current_admin)],
) -> ProductGroup:
    group = ProductGroup(name=payload.name, normalized_name=normalize_group_name(payload.name))
    db.add(group)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="分组名称已存在") from exc
    db.refresh(group)
    return group


@app.put("/api/groups/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    payload: GroupUpdate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(get_current_admin)],
) -> ProductGroup:
    group = get_group_or_404(db, group_id)
    if payload.name is not None:
        group.name = " ".join(payload.name.split())
        group.normalized_name = normalize_group_name(payload.name)
    if payload.default_sort_field is not None:
        group.default_sort_field = payload.default_sort_field
    if payload.default_sort_direction is not None:
        group.default_sort_direction = payload.default_sort_direction
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="分组名称已存在") from exc
    db.refresh(group)
    return group


@app.delete("/api/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(get_current_admin)],
) -> Response:
    group = get_group_or_404(db, group_id)
    db.delete(group)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/ingest/groups", response_model=list[GroupResponse], dependencies=[Depends(require_ingest_token)])
def ingest_groups(db: Annotated[Session, Depends(get_db)]) -> list[ProductGroup]:
    return list(db.scalars(select(ProductGroup).order_by(ProductGroup.name.asc())))


async def cache_image_task(product_id: int, image_url: str) -> None:
    try:
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            if not product:
                return
            filename = await cache_product_image(product.offer_id, image_url, settings)
            product.cached_image_name = filename
            product.image_status = "ready"
            db.commit()
    except Exception:
        logger.exception("Failed to cache image for product %s", product_id)
        with SessionLocal() as db:
            product = db.get(Product, product_id)
            if product:
                product.image_status = "failed"
                db.commit()


@app.post(
    "/api/ingest/batches",
    response_model=IngestBatchResponse,
    dependencies=[Depends(require_ingest_token)],
)
def create_ingest_batch(
    payload: IngestBatchRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    app_settings: Annotated[Settings, Depends(get_settings)],
) -> IngestBatchResponse:
    # Empty pages are valid after lazy-loading failures or abnormal-product filtering.
    # Persisting an empty batch lets the collector mark that page as uploaded and
    # preserves idempotency instead of retrying a deterministic 422 response.
    if len(payload.products) > app_settings.max_ingest_items:
        raise HTTPException(status_code=413, detail="单次上传商品数量过多")
    try:
        batch, duplicate, images = ingest_batch(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="分组不存在") from exc
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(select(ProductGroup).where(ProductGroup.id == payload.group_id))
        if not existing:
            raise HTTPException(status_code=404, detail="分组不存在") from exc
        raise HTTPException(status_code=409, detail="上传冲突，请使用相同幂等键重试") from exc
    for product_id, image_url in images:
        background_tasks.add_task(cache_image_task, product_id, image_url)
    return IngestBatchResponse(batch_id=batch.id, accepted=batch.item_count, duplicate=duplicate)


@app.get("/api/groups/{group_id}/products", response_model=ProductPage)
def group_products(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(get_current_admin)],
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    sort_field: SortField | None = None,
    sort_direction: SortDirection | None = None,
) -> ProductPage:
    group = get_group_or_404(db, group_id)
    resolved_field = sort_field or group.default_sort_field
    resolved_direction = sort_direction or group.default_sort_direction
    products, total = list_group_products(db, group_id, page, page_size, resolved_field, resolved_direction)
    return ProductPage(
        items=[product_to_item(product) for product in products],
        total=total,
        page=page,
        page_size=page_size,
        sort_field=resolved_field,
        sort_direction=resolved_direction,
    )


@app.get("/api/products/{product_id}", response_model=ProductDetail)
def product_detail(
    product_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(get_current_admin)],
) -> ProductDetail:
    product = get_product_with_relations(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    base = product_to_item(product).model_dump()
    groups = [membership.group for membership in product.memberships]
    snapshots = sorted(product.snapshots, key=lambda snapshot: snapshot.collected_at, reverse=True)[:100]
    return ProductDetail(
        **base,
        original_image_url=product.original_image_url,
        image_status=product.image_status,
        groups=groups,
        snapshots=[snapshot_to_response(snapshot) for snapshot in snapshots],
    )
