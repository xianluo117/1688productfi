from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class BusinessFields(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BatchFields(BusinessFields):
    dify_batch_number: str | None = Field(None, description="Dify 批次编号")
    username: str | None = Field(None, description="外部业务用户名，不绑定登录账号")
    autoimage_batch_number: str | None = Field(None, description="autoimage 批次编号")
    autoexcel_batch_number: str | None = Field(None, description="autoexcel 批次编号")
    submitted_at: datetime | None = Field(None, description="提交时间")
    run_started_at: datetime | None = Field(None, description="运行开始时间")
    processing_stage: str | None = Field(None, description="处理阶段，外部文本")
    final_run_result: str | None = Field(None, description="最终运行结果")
    original_item_code_summary: str | None = Field(None, description="原始货号摘要")
    spreadsheet_url_1: str | None = Field(None, description="第一份表格链接，中性编号")
    spreadsheet_url_2: str | None = Field(None, description="第二份表格链接，中性编号")
    temu_upload_result: str | None = Field(None, description="Temu 上传结果")
    exception_info: str | None = Field(None, description="异常信息")


class ItemFields(BusinessFields):
    original_item_code: str | None = Field(None, description="原始货号")
    source_url: str | None = Field(None, description="来源链接")
    new_item_code: str | None = Field(None, description="新货号，不唯一")
    username: str | None = Field(None, description="独立保存的外部业务用户名")
    original_specifications: dict[str, Any] | list[Any] | None = Field(None, description="原始规格，对象或数组，整体替换")
    processed_specifications: dict[str, Any] | list[Any] | None = Field(None, description="处理后规格，对象或数组，整体替换")
    original_sizes: dict[str, Any] | list[Any] | None = Field(None, description="原始尺码，对象或数组，整体替换")
    processed_sizes: dict[str, Any] | list[Any] | None = Field(None, description="处理后尺码，对象或数组，整体替换")
    is_discarded: bool | None = Field(None, description="是否丢弃")
    discard_reason: str | None = Field(None, description="丢弃原因")
    candidate_image_download_result: str | None = Field(None, description="候选图片下载结果")
    candidate_image_download_exception: str | None = Field(None, description="候选图片下载异常")
    candidate_generation_result: str | None = Field(None, description="候选生成结果")
    candidate_generation_exception: str | None = Field(None, description="候选生成异常")


class ColorFields(BusinessFields):
    original_color: str | None = Field(None, description="原始颜色")
    spreadsheet_standard_color: str | None = Field(None, description="制表标准颜色")
    run_result: str | None = Field(None, description="运行结果")
    main_image_visual_result: str | None = Field(None, description="主图视觉结果")
    image_generation_result: str | None = Field(None, description="生图结果")
    image_urls: dict[str, Any] | list[Any] | None = Field(None, description="图片 URL 组，对象或数组，整体替换")


class BatchCreate(BatchFields):
    pass


class BatchPatch(BatchFields):
    pass


class ItemCreate(ItemFields):
    pass


class ItemPatch(ItemFields):
    pass


class ColorCreate(ColorFields):
    pass


class ColorPatch(ColorFields):
    pass


class RecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class BatchResponse(BatchFields, RecordResponse):
    pass


class ItemResponse(ItemFields, RecordResponse):
    batch_id: int


class ColorResponse(ColorFields, RecordResponse):
    item_id: int


Record = TypeVar("Record")


class Page(BaseModel, Generic[Record]):
    items: list[Record]
    total: int
    page: int
    page_size: int


class ItemAggregate(BaseModel):
    item: ItemResponse
    batch: BatchResponse
    colors: list[ColorResponse]


class AggregateResponse(BaseModel):
    items: list[ItemAggregate]
    total: int
