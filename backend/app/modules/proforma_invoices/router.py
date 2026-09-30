"""PI 엔드포인트 — 조회·FREE 열 편집·상태이력·CSV (S3-1 design-integrated §2.7 / design-B B8).

생성(참조 생성·미리보기)과 취소는 trade_chain 라우터가 맡는다(PI는 QT에서만 만들고 상태는 전이 오케스트레이션이 바꾼다).
권한: 조회는 전 역할(원가·마진 필드가 없어 마스킹 비대상 — 은행정보·금액은 바이어에게 나가는 서류 값), 쓰기는 무역(관리자 상시 통과).
**`POST /proforma-invoices`도 `DELETE`도 없다** — PI는 QT 참조 생성으로만 태어나고 폐기는 CANCELLED 전이(사유 필수)뿐이며
행과 번호는 남는다(결번 0·번호 재사용 0). 라인 편집 API도 없다(생성=발행=동결).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.proforma_invoices import service
from app.modules.proforma_invoices.schemas import (
    PiMetaUpdateRequest,
    ProformaInvoiceDetail,
    ProformaInvoiceSummary,
    StatusLogOut,
)

CAN_WRITE = (RoleCode.TRADE,)

router = APIRouter(prefix="/proforma-invoices", tags=["proforma-invoices"])


def _filters(
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{3,20}$")] = None,
    qt_id: Annotated[int | None, Query(ge=1)] = None,
    buyer_partner_id: Annotated[int | None, Query(ge=1)] = None,
    assignee_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[str | None, Query(max_length=100, description="PI번호·바이어명 부분 일치")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "qt_id": qt_id,
        "buyer_partner_id": buyer_partner_id,
        "assignee_id": assignee_id,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
    }


Filters = Annotated[dict[str, object], Depends(_filters)]


@router.get("", summary="PI 목록 (페이지·필터)")
def list_proforma_invoices(
    current: CurrentUser, params: Annotated[PageParams, Depends()], filters: Filters
) -> Page[ProformaInvoiceSummary]:
    items, total = service.list_proforma_invoices(
        offset=params.offset,
        limit=params.limit,
        **filters,  # type: ignore[arg-type]
    )
    return Page.of([ProformaInvoiceSummary.model_validate(item) for item in items], total, params)


@router.get("/export.csv", summary="PI 목록 CSV 내보내기 (UTF-8 BOM — 은행 계좌번호 미포함)")
def export_proforma_invoices_csv(current: CurrentUser, filters: Filters) -> StreamingResponse:
    rows = service.export_rows(**filters)  # type: ignore[arg-type]
    return csv_response("PI목록.csv", service.EXPORT_HEADER, rows)


# ★ `/{pi_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{pi_id}", summary="PI 상세 (라인·은행 스냅샷·선수금 청구액 포함)")
def get_proforma_invoice(pi_id: int, current: CurrentUser) -> ProformaInvoiceDetail:
    return ProformaInvoiceDetail.model_validate(service.get_proforma_invoice(pi_id))


@router.patch(
    "/{pi_id}/meta",
    summary="내부 메모·담당자 (동결 후에도 허용 — FREE 열)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_proforma_invoice_meta(
    pi_id: int, payload: PiMetaUpdateRequest, current: CurrentUser
) -> ProformaInvoiceDetail:
    body = service.update_meta(
        actor=current, pi_id=pi_id, payload=payload.model_dump(exclude_unset=True)
    )
    return ProformaInvoiceDetail.model_validate(body)


@router.get("/{pi_id}/status-log", summary="상태 변경 이력 (최신순 · 불변 이력)")
def list_status_log(
    pi_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[StatusLogOut]:
    items, total = service.list_status_log(pi_id=pi_id, offset=params.offset, limit=params.limit)
    return Page.of([StatusLogOut.model_validate(item) for item in items], total, params)
