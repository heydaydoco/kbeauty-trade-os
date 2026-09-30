"""견적 엔드포인트 — CRUD·라인 편집·상태이력·CSV (S3-1 design-integrated §2.7 / design-B B8·B9).

권한: 조회는 전 역할(원가·마진 필드가 없어 마스킹 비대상), 쓰기(작성·편집·라인)는 **무역**(관리자 상시 통과).
전이(발행·취소·개정)는 trade_chain 라우터가 맡는다. **`DELETE /quotations/{id}`는 없다** — 폐기의 유일한 방법은
CANCELLED 전이(사유 필수)이고 행과 번호는 남는다(결번 0·번호 재사용 0). 라인 제외(DELETE …/lines/{id})만 있다.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.quotations import service
from app.modules.quotations.schemas import (
    LineAddRequest,
    LineMutationOut,
    LineUpdateRequest,
    QuotationCreateRequest,
    QuotationDetail,
    QuotationMetaUpdateRequest,
    QuotationSummary,
    QuotationUpdateRequest,
    StatusLogOut,
)

#: 견적 작성·편집은 무역이 한다(관리자는 항상 통과) — 마스터 등록과 같은 규약.
CAN_WRITE = (RoleCode.TRADE,)

router = APIRouter(prefix="/quotations", tags=["quotations"])


@router.post(
    "",
    summary="견적 작성 (DRAFT — 채번은 저장 시점, 복제는 copied_from_id)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_quotation(
    payload: QuotationCreateRequest, current: CurrentUser, key: IdempotencyKey, response: Response
) -> QuotationDetail:
    status_code, body = service.create_quotation(
        actor=current,
        idempotency_key=key,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)


def _filters(
    status: Annotated[str | None, Query(pattern=r"^[A-Z_]{3,20}$")] = None,
    buyer_partner_id: Annotated[int | None, Query(ge=1)] = None,
    assignee_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="견적번호·바이어명 부분 일치")
    ] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "buyer_partner_id": buyer_partner_id,
        "assignee_id": assignee_id,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
    }


Filters = Annotated[dict[str, object], Depends(_filters)]


@router.get("", summary="견적 목록 (페이지·필터)")
def list_quotations(
    current: CurrentUser, params: Annotated[PageParams, Depends()], filters: Filters
) -> Page[QuotationSummary]:
    items, total = service.list_quotations(offset=params.offset, limit=params.limit, **filters)  # type: ignore[arg-type]
    return Page.of([QuotationSummary.model_validate(item) for item in items], total, params)


@router.get("/export.csv", summary="견적 목록 CSV 내보내기 (UTF-8 BOM)")
def export_quotations_csv(current: CurrentUser, filters: Filters) -> StreamingResponse:
    rows = service.export_rows(**filters)  # type: ignore[arg-type]
    return csv_response("견적목록.csv", service.EXPORT_HEADER, rows)


# ★ `/{qt_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{qt_id}", summary="견적 상세 (라인 포함)")
def get_quotation(qt_id: int, current: CurrentUser) -> QuotationDetail:
    return QuotationDetail.model_validate(service.get_quotation(qt_id))


@router.patch(
    "/{qt_id}",
    summary="견적 헤더 편집 (초안만 — 동결 후 CONTENT 변경은 409, 상태는 전이로만)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_quotation(
    qt_id: int, payload: QuotationUpdateRequest, current: CurrentUser
) -> QuotationDetail:
    body = service.update_quotation(
        actor=current, qt_id=qt_id, payload=payload.model_dump(exclude_unset=True)
    )
    return QuotationDetail.model_validate(body)


@router.patch(
    "/{qt_id}/meta",
    summary="내부 메모·담당자 (동결 후에도 허용 — FREE 열)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_quotation_meta(
    qt_id: int, payload: QuotationMetaUpdateRequest, current: CurrentUser
) -> QuotationDetail:
    body = service.update_meta(
        actor=current, qt_id=qt_id, payload=payload.model_dump(exclude_unset=True)
    )
    return QuotationDetail.model_validate(body)


@router.post(
    "/{qt_id}/lines",
    summary="라인 추가 (초안만 — 마스터 값 스냅샷, 응답에 갱신된 헤더 version·합계)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def add_quotation_line(
    qt_id: int, payload: LineAddRequest, current: CurrentUser
) -> LineMutationOut:
    body = service.add_line(
        actor=current, qt_id=qt_id, payload=payload.model_dump(exclude_unset=True)
    )
    return LineMutationOut.model_validate(body)


@router.patch(
    "/{qt_id}/lines/{line_id}",
    summary="라인 수정 (초안만 — 제자리 UPDATE, SKU 변경 불가)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_quotation_line(
    qt_id: int, line_id: int, payload: LineUpdateRequest, current: CurrentUser
) -> LineMutationOut:
    body = service.update_line(
        actor=current,
        qt_id=qt_id,
        line_id=line_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return LineMutationOut.model_validate(body)


@router.delete(
    "/{qt_id}/lines/{line_id}",
    summary="라인 제외 (초안만 — soft delete, 라인 번호는 재사용하지 않는다)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def remove_quotation_line(
    qt_id: int,
    line_id: int,
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="화면이 본 헤더 version(낙관 잠금)")],
) -> LineMutationOut:
    body = service.remove_line(actor=current, qt_id=qt_id, line_id=line_id, version=version)
    return LineMutationOut.model_validate(body)


@router.get("/{qt_id}/status-log", summary="상태 변경 이력 (최신순 · 불변 이력)")
def list_status_log(
    qt_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[StatusLogOut]:
    items, total = service.list_status_log(qt_id=qt_id, offset=params.offset, limit=params.limit)
    return Page.of([StatusLogOut.model_validate(item) for item in items], total, params)
