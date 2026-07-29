"""제품 계층 요청·응답."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.catalog.models import PRODUCT_STATUSES, SKU_KINDS, SKU_STATUSES
from app.modules.catalog.service import BrandView, ProductView, SkuView

_STATUS_PATTERN = f"^({'|'.join(SKU_STATUSES)})$"
_PRODUCT_STATUS_PATTERN = f"^({'|'.join(PRODUCT_STATUSES)})$"
_KIND_PATTERN = f"^({'|'.join(SKU_KINDS)})$"


# ── 브랜드 ─────────────────────────────────────────────────────────────────


class BrandCreateRequest(BaseModel):
    brand_code: str = Field(min_length=1, max_length=20)
    name_ko: str = Field(min_length=1, max_length=100)
    name_en: str | None = Field(default=None, max_length=100)
    description: str | None = None


class BrandSummary(BaseModel):
    id: int
    brand_code: str
    name_ko: str
    name_en: str | None
    description: str | None

    @classmethod
    def of(cls, view: BrandView) -> BrandSummary:
        return cls(
            id=view.id,
            brand_code=view.brand_code,
            name_ko=view.name_ko,
            name_en=view.name_en,
            description=view.description,
        )


# ── 제품(처방) ─────────────────────────────────────────────────────────────


class ProductCreateRequest(BaseModel):
    brand_id: int
    product_code: str = Field(min_length=1, max_length=40)
    name_ko: str = Field(min_length=1, max_length=200)
    name_en: str | None = Field(default=None, max_length=200)
    description: str | None = None
    status: str = Field(default="ACTIVE", pattern=_PRODUCT_STATUS_PATTERN)


class ProductSummary(BaseModel):
    id: int
    product_code: str
    name_ko: str
    name_en: str | None
    description: str | None
    status: str
    brand_id: int
    brand_code: str
    brand_name_ko: str

    @classmethod
    def of(cls, view: ProductView) -> ProductSummary:
        return cls(
            id=view.id,
            product_code=view.product_code,
            name_ko=view.name_ko,
            name_en=view.name_en,
            description=view.description,
            status=view.status,
            brand_id=view.brand_id,
            brand_code=view.brand_code,
            brand_name_ko=view.brand_name_ko,
        )


# ── SKU ────────────────────────────────────────────────────────────────────


class SkuCreateRequest(BaseModel):
    sku_code: str = Field(min_length=1, max_length=40)
    name_ko: str = Field(min_length=1, max_length=200)
    name_en: str | None = Field(default=None, max_length=200)
    status: str = Field(default="ACTIVE", pattern=_STATUS_PATTERN)
    #: 단품이 압도적 다수라 기본값은 SINGLE이다(§4.2).
    kind: str = Field(default="SINGLE", pattern=_KIND_PATTERN)
    #: SINGLE은 필수, SET은 금지 — 짝 검증은 서비스가 한다(어느 칸이 틀렸는지
    #: 알려 주려면 필드 키가 붙은 오류여야 한다). 최종 판정자는 DB CHECK다.
    product_id: int | None = None


class SkuSummary(BaseModel):
    id: int
    sku_code: str
    name_ko: str
    name_en: str | None
    status: str
    kind: str
    product_id: int | None
    product_code: str | None
    product_name_ko: str | None
    brand_name_ko: str | None

    @classmethod
    def of(cls, view: SkuView) -> SkuSummary:
        return cls(
            id=view.id,
            sku_code=view.sku_code,
            name_ko=view.name_ko,
            name_en=view.name_en,
            status=view.status,
            kind=view.kind,
            product_id=view.product_id,
            product_code=view.product_code,
            product_name_ko=view.product_name_ko,
            brand_name_ko=view.brand_name_ko,
        )
