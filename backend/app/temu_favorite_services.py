from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import TemuGroupMembership, TemuProduct, TemuProductFavorite
from .temu_image_search_services import image_search_summaries
from .temu_services import TemuFilters, list_temu_products, listed_days


EXPORT_HEADERS = (
    "产品标题",
    "产品图片",
    "图片 URL",
    "Temu 产品链接",
    "上架天数",
    "日销量",
    "周销量",
    "1688 Top1 链接",
)


def owner_can_access_temu_product(
    db: Session,
    owner_id: int,
    product_id: int,
) -> bool:
    return bool(
        db.scalar(
            select(func.count(TemuGroupMembership.id)).where(
                TemuGroupMembership.owner_id == owner_id,
                TemuGroupMembership.product_id == product_id,
            )
        )
    )


def set_temu_favorite(
    db: Session,
    owner_id: int,
    product_id: int,
    favorite: bool,
) -> bool:
    if not owner_can_access_temu_product(db, owner_id, product_id):
        raise LookupError("Temu product not found")
    existing = db.scalar(
        select(TemuProductFavorite).where(
            TemuProductFavorite.owner_id == owner_id,
            TemuProductFavorite.product_id == product_id,
        )
    )
    if favorite and existing is None:
        db.add(TemuProductFavorite(owner_id=owner_id, product_id=product_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    elif not favorite and existing is not None:
        db.delete(existing)
        db.commit()
    return favorite


def favorite_count(db: Session, owner_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(TemuProductFavorite.id)).where(
                TemuProductFavorite.owner_id == owner_id
            )
        )
        or 0
    )


def _formula_url(value: str) -> str:
    return value.replace('"', '""')


def _safe_cell_text(value: str) -> str:
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def build_favorites_workbook(
    db: Session,
    owner_id: int,
    filters: TemuFilters,
    sort_field: str,
    sort_direction: str,
) -> tuple[bytes, int]:
    products, total = list_temu_products(
        db,
        owner_id,
        1,
        1_000_000,
        sort_field,
        sort_direction,
        filters,
        favorites_only=True,
    )
    summaries = image_search_summaries(db, [product.id for product in products])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Temu 收藏产品"
    sheet.append(EXPORT_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F5597")
    for product in products:
        summary = summaries.get(product.id) or {}
        top1 = next(
            (
                item.get("product_url")
                for item in summary.get("products", [])
                if item.get("rank") == 1
            ),
            None,
        )
        image_url = product.image_url or ""
        image_formula = (
            f'=IFERROR(IMAGE("{_formula_url(image_url)}"),"")'
            if image_url
            else ""
        )
        sheet.append(
            (
                _safe_cell_text(product.title),
                image_formula,
                image_url,
                product.product_url,
                listed_days(product.listing_date),
                product.daily_sales,
                product.weekly_sales,
                top1 or "",
            )
        )
        row = sheet.max_row
        sheet.row_dimensions[row].height = 72
        for column in (3, 4, 8):
            cell = sheet.cell(row=row, column=column)
            if cell.value:
                cell.hyperlink = str(cell.value)
                cell.style = "Hyperlink"
    widths = {"A": 48, "B": 18, "C": 52, "D": 52, "E": 12, "F": 12, "G": 12, "H": 52}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    return output.getvalue(), total
