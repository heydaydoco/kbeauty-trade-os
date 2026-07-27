"""SKU 요청·응답."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.catalog.models import SKU_STATUSES
from app.modules.catalog.service import SkuView


class SkuCreateRequest(BaseModel):
    sku_code: str = Field(min_length=1, max_length=40)
    name_ko: str = Field(min_length=1, max_length=200)
    name_en: str | None = Field(default=None, max_length=200)
    status: str = Field(default="ACTIVE", pattern=f"^({'|'.join(SKU_STATUSES)})$")


class SkuSummary(BaseModel):
    id: int
    sku_code: str
    name_ko: str
    name_en: str | None
    status: str

    @classmethod
    def of(cls, view: SkuView) -> SkuSummary:
        return cls(
            id=view.id,
            sku_code=view.sku_code,
            name_ko=view.name_ko,
            name_en=view.name_en,
            status=view.status,
        )
