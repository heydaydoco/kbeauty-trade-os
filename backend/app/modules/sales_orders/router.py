"""수주(SO) 엔드포인트 — 조회·접수 편집·라인 편집·상태이력·CSV (S3-1 design-integrated §2.7 / design-B B8·B9 / G-11).

생성(참조 생성 QT/PI→SO)과 전이(보류·재개·취소)는 trade_chain 라우터가 맡는다. 직접(인테이크) 수주의 생성은 인테이크 확정(PR-13)이 같은
착지 함수를 부른다 — **`POST /sales-orders`는 없다**. 권한: 조회는 전 역할(원가·마진 필드가 없어 마스킹 비대상), 쓰기는 무역(관리자 상시 통과).
**`DELETE /sales-orders/{id}`는 없다** — 폐기의 유일한 방법은 CANCELLED 전이(사유 필수)이고 행과 번호는 남는다(결번 0·번호 재사용 0).
라인 제외(DELETE …/lines/{id})만 있다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.sales_orders import service
from app.modules.sales_orders.schemas import (
    SalesOrderDetail,
    SalesOrderMetaUpdateRequest,
    SalesOrderSummary,
    SalesOrderUpdateRequest,
    SoLineAddRequest,
    SoLineMutationOut,
    SoLineUpdateRequest,
    StatusLogOut,
)

CAN_WRITE = (RoleCode.TRADE,)

router = APIRouter(prefix="/sales-orders", tags=["sales-orders"])


def _filters(
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{3,20}$")] = None,
    qt_id: Annotated[int | None, Query(ge=1)] = None,
    pi_id: Annotated[int | None, Query(ge=1)] = None,
    buyer_partner_id: Annotated[int | None, Query(ge=1)] = None,
    assignee_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="수주번호·바이어명·바이어 PO번호 부분 일치")
    ] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "qt_id": qt_id,
        "pi_id": pi_id,
        "buyer_partner_id": buyer_partner_id,
        "assignee_id": assignee_id,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
    }


Filters = Annotated[dict[str, object], Depends(_filters)]


@router.get("", summary="수주 목록 (페이지·필터)")
def list_sales_orders(
    current: CurrentUser, params: Annotated[PageParams, Depends()], filters: Filters
) -> Page[SalesOrderSummary]:
    items, total = service.list_sales_orders(
        offset=params.offset,
        limit=params.limit,
        **filters,  # type: ignore[arg-type]
    )
    return Page.of([SalesOrderSummary.model_validate(item) for item in items], total, params)


@router.get("/export.csv", summary="수주 목록 CSV 내보내기 (UTF-8 BOM)")
def export_sales_orders_csv(current: CurrentUser, filters: Filters) -> StreamingResponse:
    rows = service.export_rows(**filters)  # type: ignore[arg-type]
    return csv_response("수주목록.csv", service.EXPORT_HEADER, rows)


# ★ `/{so_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{so_id}", summary="수주 상세 (라인·원천 대비 차이 포함)")
def get_sales_order(so_id: int, current: CurrentUser) -> SalesOrderDetail:
    return SalesOrderDetail.model_validate(service.get_sales_order(so_id))


@router.patch(
    "/{so_id}",
    summary="수주 헤더 편집 (접수 상태만 — 확정 이후 CONTENT 변경은 409, 상태는 전이로만)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_sales_order(
    so_id: int, payload: SalesOrderUpdateRequest, current: CurrentUser
) -> SalesOrderDetail:
    body = service.update_sales_order(
        actor=current, so_id=so_id, payload=payload.model_dump(exclude_unset=True)
    )
    return SalesOrderDetail.model_validate(body)


@router.patch(
    "/{so_id}/meta",
    summary="내부 메모·담당자 (동결 후에도 허용 — FREE 열)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_sales_order_meta(
    so_id: int, payload: SalesOrderMetaUpdateRequest, current: CurrentUser
) -> SalesOrderDetail:
    body = service.update_meta(
        actor=current, so_id=so_id, payload=payload.model_dump(exclude_unset=True)
    )
    return SalesOrderDetail.model_validate(body)


@router.post(
    "/{so_id}/lines",
    summary="라인 추가 (접수 상태만 — 직접 수주는 마스터 스냅샷, 참조 수주는 원천 품목만·잔량 안에서)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def add_sales_order_line(
    so_id: int, payload: SoLineAddRequest, current: CurrentUser
) -> SoLineMutationOut:
    body = service.add_line(
        actor=current, so_id=so_id, payload=payload.model_dump(exclude_unset=True)
    )
    return SoLineMutationOut.model_validate(body)


@router.patch(
    "/{so_id}/lines/{line_id}",
    summary="라인 수정 (접수 상태만 — 제자리 UPDATE, SKU 변경 불가, 참조 수주 수량 증가는 원천 잔량 안에서)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_sales_order_line(
    so_id: int, line_id: int, payload: SoLineUpdateRequest, current: CurrentUser
) -> SoLineMutationOut:
    body = service.update_line(
        actor=current,
        so_id=so_id,
        line_id=line_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return SoLineMutationOut.model_validate(body)


@router.delete(
    "/{so_id}/lines/{line_id}",
    summary="라인 제외 (접수 상태만 — soft delete, 라인 번호는 재사용하지 않고 원천 소비량에서 즉시 빠진다)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def remove_sales_order_line(
    so_id: int,
    line_id: int,
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="화면이 본 헤더 version(낙관 잠금)")],
) -> SoLineMutationOut:
    body = service.remove_line(actor=current, so_id=so_id, line_id=line_id, version=version)
    return SoLineMutationOut.model_validate(body)


@router.get("/{so_id}/status-log", summary="상태 변경 이력 (최신순 · 불변 이력)")
def list_status_log(
    so_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[StatusLogOut]:
    items, total = service.list_status_log(so_id=so_id, offset=params.offset, limit=params.limit)
    return Page.of([StatusLogOut.model_validate(item) for item in items], total, params)
