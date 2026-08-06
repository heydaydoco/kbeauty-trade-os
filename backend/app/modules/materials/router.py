"""자재·BOM·라벨 엔드포인트 (§4.4·§4.5 / §12.2 CSV / §18.4 페이지네이션).

★ BOM 응답은 역할에 따라 **스키마가 갈린다**(ADR-0024). 원가를 볼 수 없는
  역할에게는 `unit_cost`·`currency` 필드가 아예 없는 스키마로 나간다 —
  값을 비우는 것이 아니라 모양이 다르다. 갈림은 이 파일 한 곳에서만 일어나고,
  아키텍처 테스트가 "다른 경로로 원가가 나가지 않는가"를 지킨다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode

# 헤더는 임포트 레지스트리와 한 상수다 — 내보내기와 왕복 양식이 갈라지면
# "내려받아 그대로 올리면 변화 0"(§12.2)이 그 자리에서 깨진다.
from app.modules.imports.registry import MATERIALS_CSV_HEADER as MATERIAL_CSV_HEADER
from app.modules.materials import service
from app.modules.materials.schemas import (
    BomLineCostHiddenSummary,
    BomLineCreateRequest,
    BomLineSummary,
    LabelCreateRequest,
    LabelSummary,
    MaterialCreateRequest,
    MaterialSummary,
)

#: 마스터 등록은 무역이 한다(관리자는 항상 통과) — catalog·ingredients와 같은 규약.
CAN_REGISTER = (RoleCode.TRADE,)

router = APIRouter(prefix="/materials", tags=["materials"])
#: BOM은 제품(처방)의 하위 자원, 라벨은 SKU의 하위 자원이다.
product_boms_router = APIRouter(prefix="/products", tags=["products"])
sku_labels_router = APIRouter(prefix="/skus", tags=["skus"])
#: 전체 목록 CSV는 하위 자원 경로에 얹을 수 없어 별도 접두를 쓴다 (§12.2 —
#: S1-2 관찰 항목 "라벨·BOM·규칙 목록 CSV" 종결분).
labels_router = APIRouter(prefix="/labels", tags=["labels"])
bom_export_router = APIRouter(prefix="/product-boms", tags=["products"])

LABEL_CSV_HEADER = (
    "ID",
    "SKU코드",
    "국가",
    "언어",
    "판번",
    "승인상태",
    "컷인일",
    "INCI현지검증",
    "원산지표기검증",
)

#: 원가 열 없음 — 웹 세션 승인 문면("BOM=원가 열 미포함", 마스킹 계보 준수).
BOM_CSV_HEADER = (
    "ID",
    "제품코드",
    "자재코드",
    "자재명(국문)",
    "소요량",
    "단위",
    "원산지상태",
)


# ── 자재 마스터 (§4.4) ─────────────────────────────────────────────────────


@router.post(
    "",
    summary="자재 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_material(
    payload: MaterialCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> MaterialSummary:
    status_code, body = service.create_material(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return MaterialSummary.model_validate(body)


@router.get("", summary="자재 목록")
def list_materials(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[MaterialSummary]:
    views, total = service.list_materials(offset=params.offset, limit=params.limit)
    return Page.of([MaterialSummary.of(view) for view in views], total, params)


@router.get("/export.csv", summary="자재 목록 CSV 내보내기 (UTF-8 BOM · 왕복 §12.2)")
def export_materials_csv(current: CurrentUser) -> StreamingResponse:
    """★ 단가는 자재 마스터에 없다(BOM 라인의 값이다) — 이 CSV에 원가는 없다.

    1열은 ID(왕복 diff의 키), 기본공급사는 **코드**다 — 표시명은 유일키가
    아니라 왕복 결정성이 없다(임포트 레지스트리 문서화 참조).
    """
    views = service.all_materials_for_export()
    return csv_response(
        "자재목록.csv",
        MATERIAL_CSV_HEADER,
        [
            (
                view.id,
                view.material_code,
                view.name_ko,
                view.material_type,
                view.hs6,
                view.default_supplier_code,
                view.inventory_managed,
                view.lot_managed,
            )
            for view in views
        ],
    )


# ★ `/{material_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{material_id}", summary="자재 상세")
def get_material(material_id: int, current: CurrentUser) -> MaterialSummary:
    return MaterialSummary.of(service.get_material(material_id))


# ── 자재 BOM (§4.4 / GC-B1·B2 / ADR-0024) ──────────────────────────────────


@product_boms_router.post(
    "/{product_id}/boms",
    summary="제품 BOM에 자재 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def add_bom_line(
    product_id: int,
    payload: BomLineCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> BomLineSummary:
    """★ 등록은 원가를 볼 수 있는 역할만 한다(TRADE·ADMIN) — 응답은 원가 포함."""
    status_code, body = service.add_bom_line(
        actor=current,
        idempotency_key=key,
        product_id=product_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return BomLineSummary.model_validate(body)


@product_boms_router.get(
    "/{product_id}/boms",
    summary="제품 BOM 목록 (원가는 권한 있는 역할에게만 — 필드 자체가 갈린다)",
    # ★ `response_model=None`이 **필수**다. FastAPI가 반환 타입으로 응답 모델을
    #   추론하면, Union 두 갈래 중 하나로 다시 검증하면서 우리가 고른 스키마를
    #   덮어쓴다 — 실측으로 원가 포함 응답이 원가 없는 쪽으로 재검증돼 필드가
    #   통째로 사라졌다. 여기서는 우리가 만든 스키마 인스턴스가 그대로 나간다.
    response_model=None,
    responses={200: {"model": Page[BomLineSummary]}},
)
def list_bom_lines(
    product_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[BomLineSummary] | Page[BomLineCostHiddenSummary]:
    views, total = service.list_bom_lines(
        product_id=product_id, offset=params.offset, limit=params.limit
    )
    # ★ 여기가 갈림의 유일한 지점이다. 서비스는 원가를 늘 담아 주고, 응답
    #   경계에서 역할에 따라 다른 스키마로 나간다(ADR-0024 — §18.1 "마스킹도
    #   API 응답 레벨").
    if service.may_see_bom_cost(current):
        return Page.of(
            [
                BomLineSummary.model_validate(service.serialize_bom_line(view, include_cost=True))
                for view in views
            ],
            total,
            params,
        )
    return Page.of(
        [
            BomLineCostHiddenSummary.model_validate(
                service.serialize_bom_line(view, include_cost=False)
            )
            for view in views
        ],
        total,
        params,
    )


# ── 라벨 (§4.5) ────────────────────────────────────────────────────────────


@sku_labels_router.post(
    "/{sku_id}/labels",
    summary="SKU 라벨 판 등록 (시장별·판번별)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_REGISTER)],
)
def create_label(
    sku_id: int,
    payload: LabelCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> LabelSummary:
    status_code, body = service.create_label(
        actor=current,
        idempotency_key=key,
        sku_id=sku_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return LabelSummary.model_validate(body)


@sku_labels_router.get("/{sku_id}/labels", summary="SKU 라벨 목록")
def list_sku_labels(
    sku_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[LabelSummary]:
    views, total = service.list_sku_labels(sku_id=sku_id, offset=params.offset, limit=params.limit)
    return Page.of([LabelSummary.of(view) for view in views], total, params)


@product_boms_router.get(
    "/{product_id}/labels",
    summary="제품에 딸린 SKU 라벨 롤업 (읽기 전용 — 등록은 SKU 상세에서)",
)
def list_product_labels(
    product_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[LabelSummary]:
    """§14 ③의 제품 상세 '라벨' 자리 — 실체는 SKU×시장이라 여기선 보기만 한다."""
    views, total = service.list_product_labels(
        product_id=product_id, offset=params.offset, limit=params.limit
    )
    return Page.of([LabelSummary.of(view) for view in views], total, params)


# ── 목록 CSV (§12.2 — S1-2 관찰 항목 "라벨·BOM·규칙 목록 CSV" 종결분) ─────────


@labels_router.get("/export.csv", summary="라벨 목록 CSV 내보내기 (UTF-8 BOM)")
def export_labels_csv(current: CurrentUser) -> StreamingResponse:
    """라벨 파일(아트웍)은 documents의 것이라 CSV에는 싣지 않는다."""
    views = service.all_labels_for_export()
    return csv_response(
        "라벨목록.csv",
        LABEL_CSV_HEADER,
        [
            (
                view.id,
                view.sku_code,
                view.country_code,
                view.language,
                view.label_version,
                view.approval_status,
                view.cut_in_date,
                view.inci_local_verified,
                view.origin_mark_verified,
            )
            for view in views
        ],
    )


@bom_export_router.get("/export.csv", summary="BOM 목록 CSV 내보내기 (UTF-8 BOM · 원가 열 없음)")
def export_boms_csv(current: CurrentUser) -> StreamingResponse:
    """★ 원가 열이 없다 — 역할 분기 없이 전 역할이 같은 파일을 받는다(웹 세션
    승인 문면 "BOM=원가 열 미포함"). 뷰(BomLineExportView)에 원가 필드 자체가
    없어 실수로 실릴 자리도 없다(ADR-0024의 부재 규율)."""
    views = service.all_bom_lines_for_export()
    return csv_response(
        "BOM목록.csv",
        BOM_CSV_HEADER,
        [
            (
                view.id,
                view.product_code,
                view.material_code,
                view.material_name_ko,
                view.quantity,
                view.quantity_unit,
                view.origin_status,
            )
            for view in views
        ],
    )
