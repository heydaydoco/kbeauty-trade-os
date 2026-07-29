"""제품 계층 엔드포인트 (DESIGN.md §4.1·§4.2 / §12.2 CSV / §18.4 페이지네이션).

★ 상태·종류 값은 코드 그대로 내보낸다(ACTIVE / SINGLE). 한국어 라벨은 화면이
  붙인다 — CSV에 라벨을 넣으면 S1-3의 왕복 편집(§12.2)에서 되돌릴 수 없다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.catalog import service
from app.modules.catalog.schemas import (
    BrandCreateRequest,
    BrandSummary,
    ProductCreateRequest,
    ProductSummary,
    SetComponentCreateRequest,
    SetComponentSummary,
    SkuCreateRequest,
    SkuHsCodeCreateRequest,
    SkuHsCodeSummary,
    SkuSummary,
)
from app.modules.identity.models import RoleCode

#: 마스터 등록은 무역이 한다(관리자는 항상 통과 — require_roles 규약).
CAN_REGISTER = (RoleCode.TRADE,)

brands_router = APIRouter(prefix="/brands", tags=["brands"])
products_router = APIRouter(prefix="/products", tags=["products"])
router = APIRouter(prefix="/skus", tags=["skus"])

BRAND_CSV_HEADER = ("브랜드코드", "브랜드명(국문)", "브랜드명(영문)")
PRODUCT_CSV_HEADER = ("제품코드", "제품명(국문)", "제품명(영문)", "브랜드코드", "상태")
#: 마스터 전 항목을 내보낸다 — S1-3의 왕복 편집(§12.2)이 이 양식을 그대로
#: 소비하므로, 화면에 안 보이는 칸이라고 빼면 그때 왕복이 성립하지 않는다.
SKU_CSV_HEADER = (
    "품번",
    "품명(국문)",
    "품명(영문)",
    "종류",
    "제품코드",
    "제품명(국문)",
    "브랜드명(국문)",
    "상태",
    "바코드",
    "중량(g)",
    "박스입수",
    "사용기한(개월)",
    "제조사",
    "위험물",
    "UN번호",
    "Class",
    "포장등급",
    "인화점(℃)",
    "알코올함량(%)",
    "에어로졸",
    "LQ",
    "MSDS링크",
)


# ── 브랜드 ─────────────────────────────────────────────────────────────────


@brands_router.post(
    "",
    summary="브랜드 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_brand(
    payload: BrandCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> BrandSummary:
    status_code, body = service.create_brand(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return BrandSummary.model_validate(body)


@brands_router.get("", summary="브랜드 목록")
def list_brands(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[BrandSummary]:
    views, total = service.list_brands(offset=params.offset, limit=params.limit)
    return Page.of([BrandSummary.of(view) for view in views], total, params)


@brands_router.get("/export.csv", summary="브랜드 목록 CSV 내보내기 (UTF-8 BOM)")
def export_brands_csv(current: CurrentUser) -> StreamingResponse:
    views = service.all_brands_for_export()
    return csv_response(
        "브랜드목록.csv",
        BRAND_CSV_HEADER,
        [(view.brand_code, view.name_ko, view.name_en) for view in views],
    )


# ★ `/{brand_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다. 앞에 두면
#   FastAPI가 "export.csv"를 brand_id로 해석해 422를 낸다.
@brands_router.get("/{brand_id}", summary="브랜드 상세")
def get_brand(brand_id: int, current: CurrentUser) -> BrandSummary:
    return BrandSummary.of(service.get_brand(brand_id))


# ── 제품(처방) ─────────────────────────────────────────────────────────────


@products_router.post(
    "",
    summary="제품(처방) 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_product(
    payload: ProductCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ProductSummary:
    status_code, body = service.create_product(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return ProductSummary.model_validate(body)


@products_router.get("", summary="제품 목록")
def list_products(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ProductSummary]:
    views, total = service.list_products(offset=params.offset, limit=params.limit)
    return Page.of([ProductSummary.of(view) for view in views], total, params)


@products_router.get("/export.csv", summary="제품 목록 CSV 내보내기 (UTF-8 BOM)")
def export_products_csv(current: CurrentUser) -> StreamingResponse:
    views = service.all_products_for_export()
    return csv_response(
        "제품목록.csv",
        PRODUCT_CSV_HEADER,
        [
            (view.product_code, view.name_ko, view.name_en, view.brand_code, view.status)
            for view in views
        ],
    )


@products_router.get("/{product_id}", summary="제품 상세")
def get_product(product_id: int, current: CurrentUser) -> ProductSummary:
    return ProductSummary.of(service.get_product(product_id))


# ── SKU ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    summary="SKU 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_sku(
    payload: SkuCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SkuSummary:
    status_code, body = service.create_sku(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return SkuSummary.model_validate(body)


@router.get("", summary="SKU 목록")
def list_skus(current: CurrentUser, params: Annotated[PageParams, Depends()]) -> Page[SkuSummary]:
    views, total = service.list_skus(offset=params.offset, limit=params.limit)
    return Page.of([SkuSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="SKU 목록 CSV 내보내기 (UTF-8 BOM)")
def export_skus_csv(current: CurrentUser) -> StreamingResponse:
    views = service.all_skus_for_export()
    return csv_response(
        "SKU목록.csv",
        SKU_CSV_HEADER,
        [
            (
                view.sku_code,
                view.name_ko,
                view.name_en,
                view.kind,
                view.product_code,
                view.product_name_ko,
                view.brand_name_ko,
                view.status,
                view.barcode,
                view.unit_weight_g,
                view.box_qty,
                view.shelf_life_months,
                view.manufacturer_name,
                view.dg_flag,
                view.un_number,
                view.dg_class,
                view.packing_group,
                view.flash_point_c,
                view.alcohol_content_pct,
                view.is_aerosol,
                view.is_limited_quantity,
                view.msds_url,
            )
            for view in views
        ],
    )


@router.get("/{sku_id}", summary="SKU 상세")
def get_sku(sku_id: int, current: CurrentUser) -> SkuSummary:
    return SkuSummary.of(service.get_sku(sku_id))


# ── 국가별 HS 세번 (§4.1 / ADR-03 / ADR-0019) ──────────────────────────────
#
# ★ 여기에 "HS 추천"·"자동 판정" 엔드포인트를 추가하지 않는다 (§1 비범위).
#   부재는 tests/architecture/test_no_hs_auto_classification.py가 지킨다.


@router.post(
    "/{sku_id}/hs-codes",
    summary="SKU 국가별 HS 세번 등록 (사람이 확인한 값의 기록)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_sku_hs_code(
    sku_id: int,
    payload: SkuHsCodeCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SkuHsCodeSummary:
    status_code, body = service.create_sku_hs_code(
        actor=current,
        idempotency_key=key,
        sku_id=sku_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return SkuHsCodeSummary.model_validate(body)


@router.get("/{sku_id}/hs-codes", summary="SKU 국가별 HS 세번 목록")
def list_sku_hs_codes(
    sku_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[SkuHsCodeSummary]:
    views, total = service.list_sku_hs_codes(
        sku_id=sku_id, offset=params.offset, limit=params.limit
    )
    return Page.of([SkuHsCodeSummary.of(view) for view in views], total, params)


# ── 세트 구성 (§4.2 / GC-E1) ───────────────────────────────────────────────


@router.post(
    "/{sku_id}/components",
    summary="세트 구성품 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def add_set_component(
    sku_id: int,
    payload: SetComponentCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> SetComponentSummary:
    status_code, body = service.add_set_component(
        actor=current,
        idempotency_key=key,
        set_sku_id=sku_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return SetComponentSummary.model_validate(body)


@router.get("/{sku_id}/components", summary="세트 구성 목록")
def list_set_components(
    sku_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[SetComponentSummary]:
    views, total = service.list_set_components(
        set_sku_id=sku_id, offset=params.offset, limit=params.limit
    )
    return Page.of([SetComponentSummary.of(view) for view in views], total, params)
