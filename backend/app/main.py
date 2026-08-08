from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from alembic import command
from alembic.config import Config
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    bootstrap_admin,
    create_session_token,
    get_current_user,
    get_ingest_user,
    hash_password,
    issue_ingest_token,
    require_admin,
    verify_password,
)
from .config import Settings, get_settings
from .database import SessionLocal, get_db
from .image_cache import cache_product_image
from .models import AdminUser, Product, ProductGroup, ProductGroupMembership
from .schemas import (
    CurrentUserResponse,
    GroupCreate,
    GroupResponse,
    GroupUpdate,
    IngestBatchRequest,
    IngestBatchResponse,
    LoginRequest,
    PasswordReset,
    ProductDetail,
    ProductPage,
    SortDirection,
    SortField,
    TokenResetResponse,
    UserCreate,
    UserCreatedResponse,
    UserResponse,
    UserStatusUpdate,
)
from .services import (
    ProductFilters,
    get_or_create_group,
    get_product_with_relations,
    ingest_batch,
    list_group_products,
    normalize_group_name,
    product_to_item,
    snapshot_to_response,
)
from .temu_router import router as temu_router


logger = logging.getLogger(__name__)
settings = get_settings()


def run_migrations() -> None:
    alembic_config = Config("alembic.ini")
    command.upgrade(alembic_config, "head")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.image_storage_path.mkdir(parents=True, exist_ok=True)
    for attempt in range(30):
        try:
            run_migrations()
            with SessionLocal() as db:
                bootstrap_admin(db, settings)
            break
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(2)
    yield


app = FastAPI(title="Product Manager", version="3.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/images", StaticFiles(directory=settings.image_storage_path, check_dir=False), name="images")
app.include_router(temu_router)


def current_user_response(user: AdminUser) -> CurrentUserResponse:
    return CurrentUserResponse(id=user.id, username=user.username, role=user.role, is_active=user.is_active)


def resolve_owner_id(current_user: AdminUser, requested_owner_id: int | None) -> int:
    if current_user.role != "admin":
        if requested_owner_id is not None and requested_owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权访问其他用户的数据")
        return current_user.id
    return requested_owner_id or current_user.id


def get_group_or_404(db: Session, group_id: int, current_user: AdminUser) -> ProductGroup:
    group = db.get(ProductGroup, group_id)
    if not group or (current_user.role != "admin" and group.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="分组不存在")
    return group


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=CurrentUserResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    app_settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUserResponse:
    user = db.scalar(select(AdminUser).where(AdminUser.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    response.set_cookie(
        "user_session",
        create_session_token(user, app_settings),
        max_age=app_settings.session_max_age,
        httponly=True,
        secure=app_settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return current_user_response(user)


@app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response.delete_cookie("user_session", path="/")
    response.delete_cookie("admin_session", path="/")
    return response


@app.get("/api/auth/me", response_model=CurrentUserResponse)
def me(user: Annotated[AdminUser, Depends(get_current_user)]) -> CurrentUserResponse:
    return current_user_response(user)


@app.get("/api/admin/users", response_model=list[UserResponse])
def list_users(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(require_admin)],
) -> list[AdminUser]:
    return list(db.scalars(select(AdminUser).order_by(AdminUser.username.asc())))


@app.post("/api/admin/users", response_model=UserCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(require_admin)],
) -> UserCreatedResponse:
    user = AdminUser(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    token = issue_ingest_token(user)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    db.refresh(user)
    return UserCreatedResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        ingest_token_prefix=user.ingest_token_prefix,
        ingest_token_updated_at=user.ingest_token_updated_at,
        created_at=user.created_at,
        ingest_token=token,
    )


@app.put("/api/admin/users/{user_id}/status", response_model=UserResponse)
def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    admin: Annotated[AdminUser, Depends(require_admin)],
) -> AdminUser:
    user = db.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == admin.id and not payload.is_active:
        raise HTTPException(status_code=400, detail="不能禁用当前管理员账号")
    user.is_active = payload.is_active
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/admin/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    user_id: int,
    payload: PasswordReset,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(require_admin)],
) -> Response:
    user = db.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.password_hash = hash_password(payload.password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/admin/users/{user_id}/reset-ingest-token", response_model=TokenResetResponse)
def reset_ingest_token(
    user_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[AdminUser, Depends(require_admin)],
) -> TokenResetResponse:
    user = db.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    token = issue_ingest_token(user)
    db.commit()
    db.refresh(user)
    return TokenResetResponse(
        ingest_token=token,
        ingest_token_prefix=user.ingest_token_prefix or "",
        ingest_token_updated_at=user.ingest_token_updated_at,
    )


@app.get("/api/groups", response_model=list[GroupResponse])
def list_groups(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    owner_id: int | None = Query(default=None, gt=0),
) -> list[ProductGroup]:
    if current_user.role == "admin" and owner_id is None:
        return list(db.scalars(select(ProductGroup).order_by(ProductGroup.owner_id, ProductGroup.name.asc())))
    resolved_owner_id = resolve_owner_id(current_user, owner_id)
    return list(db.scalars(
        select(ProductGroup)
        .where(ProductGroup.owner_id == resolved_owner_id)
        .order_by(ProductGroup.name.asc())
    ))


@app.post("/api/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    owner_id: int | None = Query(default=None, gt=0),
) -> ProductGroup:
    resolved_owner_id = resolve_owner_id(current_user, owner_id)
    group = ProductGroup(
        owner_id=resolved_owner_id,
        name=payload.name,
        normalized_name=normalize_group_name(payload.name),
    )
    db.add(group)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该用户下分组名称已存在") from exc
    db.refresh(group)
    return group


@app.put("/api/groups/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: int,
    payload: GroupUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
) -> ProductGroup:
    group = get_group_or_404(db, group_id, current_user)
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
        raise HTTPException(status_code=409, detail="该用户下分组名称已存在") from exc
    db.refresh(group)
    return group


@app.delete("/api/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
) -> Response:
    group = get_group_or_404(db, group_id, current_user)
    db.delete(group)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/ingest/groups", response_model=list[GroupResponse])
def ingest_groups(
    db: Annotated[Session, Depends(get_db)],
    ingest_user: Annotated[AdminUser, Depends(get_ingest_user)],
) -> list[ProductGroup]:
    return list(db.scalars(
        select(ProductGroup)
        .where(ProductGroup.owner_id == ingest_user.id)
        .order_by(ProductGroup.name.asc())
    ))


@app.post("/api/ingest/groups", response_model=GroupResponse)
def ensure_ingest_group(
    payload: GroupCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    ingest_user: Annotated[AdminUser, Depends(get_ingest_user)],
) -> ProductGroup:
    group, created = get_or_create_group(db, ingest_user.id, payload.name)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return group


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


@app.post("/api/ingest/batches", response_model=IngestBatchResponse)
def create_ingest_batch(
    payload: IngestBatchRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    ingest_user: Annotated[AdminUser, Depends(get_ingest_user)],
    app_settings: Annotated[Settings, Depends(get_settings)],
) -> IngestBatchResponse:
    if len(payload.products) > app_settings.max_ingest_items:
        raise HTTPException(status_code=413, detail="单次上传商品数量过多")
    try:
        batch, duplicate, images = ingest_batch(db, ingest_user, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="分组不存在或不属于当前令牌用户") from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="上传冲突，请使用相同幂等键重试") from exc
    for product_id, image_url in images:
        background_tasks.add_task(cache_image_task, product_id, image_url)
    return IngestBatchResponse(batch_id=batch.id, accepted=batch.item_count, duplicate=duplicate)


@app.get("/api/groups/{group_id}/products", response_model=ProductPage)
def group_products(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    sort_field: SortField | None = None,
    sort_direction: SortDirection | None = None,
    listing_date_from: date | None = None,
    listing_date_to: date | None = None,
    listed_days_min: int | None = Query(default=None, ge=0),
    listed_days_max: int | None = Query(default=None, ge=0),
    total_orders_min: int | None = Query(default=None, ge=0),
    total_orders_max: int | None = Query(default=None, ge=0),
    total_pieces_min: int | None = Query(default=None, ge=0),
    total_pieces_max: int | None = Query(default=None, ge=0),
    daily_orders_min: Decimal | None = Query(default=None, ge=0),
    daily_orders_max: Decimal | None = Query(default=None, ge=0),
    collected_at_from: datetime | None = None,
    collected_at_to: datetime | None = None,
) -> ProductPage:
    group = get_group_or_404(db, group_id, current_user)
    resolved_field = sort_field or group.default_sort_field
    resolved_direction = sort_direction or group.default_sort_direction
    filters = ProductFilters(
        listing_date_from=listing_date_from,
        listing_date_to=listing_date_to,
        listed_days_min=listed_days_min,
        listed_days_max=listed_days_max,
        total_orders_min=total_orders_min,
        total_orders_max=total_orders_max,
        total_pieces_min=total_pieces_min,
        total_pieces_max=total_pieces_max,
        daily_orders_min=daily_orders_min,
        daily_orders_max=daily_orders_max,
        collected_at_from=collected_at_from,
        collected_at_to=collected_at_to,
    )
    products, total = list_group_products(
        db, group_id, group.owner_id, page, page_size, resolved_field, resolved_direction, filters
    )
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
    current_user: Annotated[AdminUser, Depends(get_current_user)],
    owner_id: int | None = Query(default=None, gt=0),
) -> ProductDetail:
    resolved_owner_id = resolve_owner_id(current_user, owner_id)
    product = get_product_with_relations(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    memberships = [item for item in product.memberships if item.owner_id == resolved_owner_id]
    if not memberships and current_user.role != "admin":
        raise HTTPException(status_code=404, detail="商品不存在")
    base = product_to_item(product).model_dump()
    snapshots = sorted(
        (snapshot for snapshot in product.snapshots if snapshot.owner_id == resolved_owner_id),
        key=lambda snapshot: snapshot.collected_at,
        reverse=True,
    )[:100]
    return ProductDetail(
        **base,
        original_image_url=product.original_image_url,
        image_status=product.image_status,
        groups=[membership.group for membership in memberships],
        snapshots=[snapshot_to_response(snapshot) for snapshot in snapshots],
    )
