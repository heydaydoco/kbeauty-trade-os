"""PO 엔드포인트 — 생성(=발행=발주 확정)·미리보기·조회·FREE 메타·상태이력·CSV (S3-1 design-integrated §2.7 / design-F F2·F5·F8·F15 / ADR-0057).

전이(공급사 확인[OC]·취소)는 trade_chain 라우터가 맡는다(`POST /purchase-orders/{id}/transitions` — 전이 오케스트레이션은 L2).
권한: 조회는 전 역할이되 **원가를 볼 수 없는 역할(VIEWER)은 원가·통화·가격 필드가 없는 응답**(200 — 행·건수·상태는 보인다, ADR-0024 필드 부재),
쓰기(생성·미리보기·메타)는 무역(관리자 상시 통과 — 모두 원가 가시 역할). **`DELETE`는 없다** — 폐기는 CANCELLED 전이(사유 필수)뿐이고 행·번호는 남는다.

★ **이 모듈의 모든 엔드포인트는 `response_model=None`**이다 — FastAPI가 반환 타입(Union)으로 응답 모델을 추론하면 한 갈래로 재검증하며 원가 필드를 지우거나
  다른 스키마로 덮어쓴다(BOM 마스킹 실측 함정). 스키마 선택은 **이 파일의 `detail_response`·`page_response` 한 곳**(`may_see_po_cost`)이다.
★ **`create_purchase_order`의 유일한 호출처가 이 라우터다**(자동 발주 경로 부재 — test_no_auto_confirm_code_path_exists). 스케줄러·CLI·outbox·imports·
  order_intake·order_board·approvals·gates는 이 함수를 임포트하지 못한다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.purchase_orders import service
from app.modules.purchase_orders.schemas import (
    PoKindLiteral,
    PoSortKey,
    PurchaseOrderCostHiddenDetail,
    PurchaseOrderCostHiddenSummary,
    PurchaseOrderCreateRequest,
    PurchaseOrderDetail,
    PurchaseOrderMetaUpdateRequest,
    PurchaseOrderPreview,
    PurchaseOrderSummary,
    StatusLogOut,
)

CAN_WRITE = (RoleCode.TRADE,)

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


# ── 응답 경계: 원가 스키마 갈림은 여기 한 곳이다 ───────────────────────────────────


def detail_response(
    actor: AuthenticatedUser, body: dict[str, Any]
) -> PurchaseOrderDetail | PurchaseOrderCostHiddenDetail:
    """서비스가 만든 dict를 역할에 맞는 스키마 인스턴스로 — 원가 없는 스키마에는 원가를 담을 자리가 없다."""
    if service.may_see_po_cost(actor):
        return PurchaseOrderDetail.model_validate(body)
    return PurchaseOrderCostHiddenDetail.model_validate(body)


def _filters(
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{3,20}$")] = None,
    supplier_partner_id: Annotated[int | None, Query(ge=1)] = None,
    po_kind: Annotated[PoKindLiteral | None, Query()] = None,
    assignee_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="발주번호·공급사명·SKU 코드 부분 일치")
    ] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, object]:
    # ★ 원가 범위 필터 파라미터는 **만들지 않는다**(ADR-0057 ⑤-1) — 원가 값으로 좁히는 길이 없다.
    return {
        "status": status,
        "supplier_partner_id": supplier_partner_id,
        "po_kind": po_kind,
        "assignee_id": assignee_id,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
    }


Filters = Annotated[dict[str, object], Depends(_filters)]


@router.get(
    "",
    summary="PO 목록 (페이지·필터·정렬 — 원가는 권한 있는 역할에게만, 필드 자체가 갈린다)",
    response_model=None,
    responses={200: {"model": Page[PurchaseOrderSummary]}},
)
def list_purchase_orders(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    filters: Filters,
    sort: Annotated[
        PoSortKey | None, Query(description="정렬 키 — 원가 값은 정렬 대상이 아니다(422)")
    ] = None,
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
) -> Page[PurchaseOrderSummary] | Page[PurchaseOrderCostHiddenSummary]:
    include_cost = service.may_see_po_cost(current)
    items, total = service.list_purchase_orders(
        offset=params.offset,
        limit=params.limit,
        include_cost=include_cost,
        sort=sort,
        descending=order == "desc",
        **filters,  # type: ignore[arg-type]
    )
    if include_cost:
        return Page.of([PurchaseOrderSummary.model_validate(item) for item in items], total, params)
    return Page.of(
        [PurchaseOrderCostHiddenSummary.model_validate(item) for item in items], total, params
    )


@router.get(
    "/export.csv",
    summary="PO 목록 CSV 내보내기 (UTF-8 BOM — 원가 없는 역할은 원가·통화 열이 없는 헤더)",
    response_model=None,
)
def export_purchase_orders_csv(current: CurrentUser, filters: Filters) -> StreamingResponse:
    include_cost = service.may_see_po_cost(current)
    rows = service.export_rows(include_cost=include_cost, **filters)  # type: ignore[arg-type]
    return csv_response("발주서목록.csv", service.export_header(include_cost=include_cost), rows)


@router.post(
    "/preview",
    summary="PO 미리보기 (비저장 — 채번·이벤트·멱등 키 소비 없음, 생성과 같은 검증·단가 계산)",
    response_model=None,
    responses={200: {"model": PurchaseOrderPreview}},
    dependencies=[require_roles(*CAN_WRITE)],
)
def preview_purchase_order(
    payload: PurchaseOrderCreateRequest, current: CurrentUser
) -> PurchaseOrderPreview:
    body = service.preview_purchase_order(
        actor=current, payload=payload.model_dump(exclude_unset=True)
    )
    return PurchaseOrderPreview.model_validate(body)


@router.post(
    "",
    summary="PO 생성 (= 발행 = 발주 확정 — 사람 1클릭·Idempotency-Key 필수, 초안 없음)",
    status_code=status.HTTP_201_CREATED,
    response_model=None,
    responses={201: {"model": PurchaseOrderDetail}},
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_purchase_order(
    payload: PurchaseOrderCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> PurchaseOrderDetail | PurchaseOrderCostHiddenDetail:
    status_code, body = service.create_purchase_order(
        actor=current, idempotency_key=key, payload=payload.model_dump(exclude_unset=True)
    )
    response.status_code = status_code
    return detail_response(current, body)


# ★ `/{po_id}`는 `/export.csv`·`/preview`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get(
    "/{po_id}",
    summary="PO 상세 (라인 포함 — 원가는 권한 있는 역할에게만)",
    response_model=None,
    responses={200: {"model": PurchaseOrderDetail}},
)
def get_purchase_order(
    po_id: int, current: CurrentUser
) -> PurchaseOrderDetail | PurchaseOrderCostHiddenDetail:
    body = service.get_purchase_order(po_id, include_cost=service.may_see_po_cost(current))
    return detail_response(current, body)


@router.patch(
    "/{po_id}/meta",
    summary="내부 메모·담당자·OC 일자·OC 참조 (동결 후에도 허용 — FREE 열)",
    response_model=None,
    responses={200: {"model": PurchaseOrderDetail}},
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_purchase_order_meta(
    po_id: int, payload: PurchaseOrderMetaUpdateRequest, current: CurrentUser
) -> PurchaseOrderDetail | PurchaseOrderCostHiddenDetail:
    body = service.update_meta(
        actor=current, po_id=po_id, payload=payload.model_dump(exclude_unset=True)
    )
    return detail_response(current, body)


@router.get(
    "/{po_id}/status-log",
    summary="상태 변경 이력 (최신순 · 불변 이력)",
    response_model=None,
    responses={200: {"model": Page[StatusLogOut]}},
)
def list_status_log(
    po_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[StatusLogOut]:
    items, total = service.list_status_log(po_id=po_id, offset=params.offset, limit=params.limit)
    return Page.of([StatusLogOut.model_validate(item) for item in items], total, params)
