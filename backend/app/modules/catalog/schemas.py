"""제품 계층 요청·응답."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.catalog.models import (
    PRICE_TYPES,
    PRODUCT_STATUSES,
    SKU_KINDS,
    SKU_STATUSES,
)
from app.modules.catalog.pricing import SkuPriceView
from app.modules.catalog.service import (
    BrandView,
    ProductView,
    SetComponentView,
    SkuHsCodeView,
    SkuView,
)

_STATUS_PATTERN = f"^({'|'.join(SKU_STATUSES)})$"
_PRODUCT_STATUS_PATTERN = f"^({'|'.join(PRODUCT_STATUSES)})$"
_KIND_PATTERN = f"^({'|'.join(SKU_KINDS)})$"
_PRICE_TYPE_PATTERN = f"^({'|'.join(PRICE_TYPES)})$"


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

    # 물류 (§4.1)
    barcode: str | None = Field(default=None, max_length=20)
    #: 소매포장 포함 판매단위 1개의 중량(g). 선적의 G.W./N.W.와 다르다(ADR-0003 ⑧).
    unit_weight_g: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    box_qty: int | None = Field(default=None, gt=0)
    shelf_life_months: int | None = Field(default=None, gt=0)
    manufacturer_name: str | None = Field(default=None, max_length=100)

    # DG (§4.1 / §7.7). 완결성·교차 정합은 P4 DG 게이트가 본다(ADR-0016 부기).
    dg_flag: bool = False
    un_number: str | None = Field(default=None, max_length=10)
    dg_class: str | None = Field(default=None, max_length=10)
    packing_group: str | None = Field(default=None, max_length=3)
    #: 인화점은 음수도 실재한다(에탄올 13℃, 더 낮은 것도 있다) — 하한을 걸지 않는다.
    flash_point_c: Decimal | None = Field(default=None, max_digits=5, decimal_places=1)
    alcohol_content_pct: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    is_aerosol: bool = False
    is_limited_quantity: bool = False
    msds_url: str | None = Field(default=None, max_length=500)


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
    barcode: str | None
    unit_weight_g: Decimal | None
    box_qty: int | None
    shelf_life_months: int | None
    manufacturer_name: str | None
    dg_flag: bool
    un_number: str | None
    dg_class: str | None
    packing_group: str | None
    flash_point_c: Decimal | None
    alcohol_content_pct: Decimal | None
    is_aerosol: bool
    is_limited_quantity: bool
    msds_url: str | None

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
            barcode=view.barcode,
            unit_weight_g=view.unit_weight_g,
            box_qty=view.box_qty,
            shelf_life_months=view.shelf_life_months,
            manufacturer_name=view.manufacturer_name,
            dg_flag=view.dg_flag,
            un_number=view.un_number,
            dg_class=view.dg_class,
            packing_group=view.packing_group,
            flash_point_c=view.flash_point_c,
            alcohol_content_pct=view.alcohol_content_pct,
            is_aerosol=view.is_aerosol,
            is_limited_quantity=view.is_limited_quantity,
            msds_url=view.msds_url,
        )


# ── 국가별 HS 세번 (§4.1 / ADR-03 / ADR-0019) ──────────────────────────────


class SkuHsCodeCreateRequest(BaseModel):
    """HS 세번 **기록** 요청 — 시스템은 판정하지 않는다(§1 비범위)."""

    #: ISO 3166-1 alpha-2. 소문자로 와도 서비스가 대문자로 정규화한다.
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    #: "HS2022" 형식. 값 목록이 아니라 형식만 본다(WCO 개정 대비).
    hs_version: str = Field(max_length=10, pattern=r"^[Hh][Ss][0-9]{4}$")
    #: 점·공백이 섞여 와도 받는다 — 서비스가 숫자만 남긴다.
    hs_code: str = Field(min_length=6, max_length=20)
    #: 세율 **메모**. 계산에 쓰지 않는다.
    tariff_note: str | None = None
    #: ADR-03: 근거링크 필수. 열어볼 수 없는 값(예: "확인함")이 들어오지 않게
    #: http(s)로 제한한다 — 링크가 아니면 근거가 아니다.
    source_url: str = Field(max_length=500, pattern=r"^https?://")
    #: 최종확인일(업무 날짜). 미래 날짜는 서비스가 거절한다.
    last_verified_on: date


class SkuHsCodeSummary(BaseModel):
    id: int
    sku_id: int
    country_code: str
    hs_version: str
    hs_code: str
    tariff_note: str | None
    source_url: str
    last_verified_on: date

    @classmethod
    def of(cls, view: SkuHsCodeView) -> SkuHsCodeSummary:
        return cls(
            id=view.id,
            sku_id=view.sku_id,
            country_code=view.country_code,
            hs_version=view.hs_version,
            hs_code=view.hs_code,
            tariff_note=view.tariff_note,
            source_url=view.source_url,
            last_verified_on=view.last_verified_on,
        )


# ── 세트 구성 (§4.2 / GC-E1) ───────────────────────────────────────────────


class SetComponentCreateRequest(BaseModel):
    component_sku_id: int
    #: 세트 1개당 수량. 원장은 EA 단일이다(§8.2).
    quantity: int = Field(gt=0)


class SetComponentSummary(BaseModel):
    id: int
    set_sku_id: int
    component_sku_id: int
    component_sku_code: str
    component_name_ko: str
    component_shelf_life_months: int | None
    quantity: int

    @classmethod
    def of(cls, view: SetComponentView) -> SetComponentSummary:
        return cls(
            id=view.id,
            set_sku_id=view.set_sku_id,
            component_sku_id=view.component_sku_id,
            component_sku_code=view.component_sku_code,
            component_name_ko=view.component_name_ko,
            component_shelf_life_months=view.component_shelf_life_months,
            quantity=view.quantity,
        )


# ── 단가 이력 (§4.1 / ADR-0017·0018) ───────────────────────────────────────


class SkuPriceCreateRequest(BaseModel):
    """단가 등록 요청.

    ★ 금액은 **사람이 쓰는 표기**로 받는다(12000 / 12.34). 서버가 통화별
      자릿수로 정수 최소단위로 바꾼다 — 화면마다 환산하면 그 규칙이 갈린다.
    """

    price_type: str = Field(pattern=_PRICE_TYPE_PATTERN)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    #: Decimal로 받는다. float으로 받으면 12.34가 12.339999…로 들어온다(§2 ADR-02).
    amount: Decimal = Field(ge=0)
    effective_from: date
    note: str | None = None


class SkuPriceSummary(BaseModel):
    id: int
    sku_id: int
    price_type: str
    currency: str
    #: 정수 최소단위. 표시 변환은 화면이 /v1/system/currencies의 자릿수로 한다.
    amount: int
    effective_from: date
    note: str | None
    is_current: bool

    @classmethod
    def of(cls, view: SkuPriceView) -> SkuPriceSummary:
        return cls(
            id=view.id,
            sku_id=view.sku_id,
            price_type=view.price_type,
            currency=view.currency,
            amount=view.amount,
            effective_from=view.effective_from,
            note=view.note,
            is_current=view.is_current,
        )
