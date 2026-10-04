"""선적 엔드포인트 — 목록·CSV·상세·상태이력·SO 참조 생성(미리보기·생성)·헤더·라인·출고지시·취소·당사자 (S3-2 PR-3a·PR-3c / design-D D2-1 S1~S20 / ADR-0079).

권한은 **경로 단위**다(라우터 가드만으로 403이 존재 검사 전에 성립 — 401→403→404→409→422, `D:370`):
  조회(목록·CSV·상세·이력) = 전 역할 / 생성·미리보기·라인·취소 = 무역(SO 잔량 소비·SO 수렴 = 상업 사실) / 헤더(메모·담당·국가)·출고지시·당사자 = 무역 + **물류**
  (물류 첫 전표 쓰기 — ADR-0079). 관리자는 `require_roles`에서 상시 통과한다.
쓰기는 전부 사람 1클릭이고 생성·라인 추가·출고지시·취소·당사자 추가는 `Idempotency-Key` 필수, 기존 행 수정은 `version` 필수(409).
이 라우터가 `create_shipment_from_sales_order`·`release_shipment_order`·`transition_shipment`의 **유일한 호출처**다(자동 선적·자동 출고지시 0 —
test_no_auto_confirm_code_path_exists). 출고지시는 범용 `/transitions`로 못 넘는다(`to_status` Literal = 취소 1값).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.shipments.schemas import (
    PartyAddRequest,
    ShipmentCreateFromSo,
    ShipmentDetail,
    ShipmentLineAddRequest,
    ShipmentLineUpdateRequest,
    ShipmentListItem,
    ShipmentPreview,
    ShipmentTarget,
    ShipmentTransitionRequest,
    ShipmentUpdateRequest,
    ShipmentVersionRequest,
    StatusLogOut,
    shipment_detail_out,
    shipment_list_item_out,
)
from app.modules.trade_chain import shipment_flow, shipment_view
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import public_transition_targets

#: 생성·라인·취소 — 무역(관리자 상시 통과).
CAN_WRITE = (RoleCode.TRADE,)
#: 헤더·출고지시·당사자 — 무역 + 물류(ADR-0079 물류 첫 전표 쓰기).
CAN_OPERATE = (RoleCode.TRADE, RoleCode.LOGISTICS)

assert set(ShipmentTarget.__args__) == public_transition_targets(DocKind.SHIPMENT)  # type: ignore[attr-defined]

router = APIRouter(prefix="/shipments", tags=["shipments"])
so_router = APIRouter(prefix="/sales-orders", tags=["shipments"])


# ── SO 참조 생성 ──────────────────────────────────────────────────────────────


@so_router.post(
    "/{so_id}/shipments/preview",
    summary="수출선적 미리보기 (비저장 — 채번·이벤트·멱등 키 소비 0, 생성과 같은 검증)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def preview_shipment(
    so_id: Annotated[int, Path(ge=1)], payload: ShipmentCreateFromSo, current: CurrentUser
) -> ShipmentPreview:
    body = shipment_flow.preview_shipment_from_sales_order(
        actor=current, so_id=so_id, payload=payload.model_dump()
    )
    return ShipmentPreview.model_validate(body)


@so_router.post(
    "/{so_id}/shipments",
    summary="SO 참조 수출선적 생성 (계획 PLANNED — 원천 사본·잔량 안에서, 첫 선적이면 SO가 같은 TX에서 선적중으로)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_shipment(
    so_id: Annotated[int, Path(ge=1)],
    payload: ShipmentCreateFromSo,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ShipmentDetail:
    status_code, body = shipment_flow.create_shipment_from_sales_order(
        actor=current, idempotency_key=key, so_id=so_id, payload=payload.model_dump()
    )
    response.status_code = status_code
    return shipment_detail_out(body)


# ── 조회 ───────────────────────────────────────────────────────────────────


def _filters(
    status: Annotated[
        list[str] | None, Query(description="상태(여러 개 가능)", max_length=8)
    ] = None,
    shipment_kind: Annotated[str | None, Query(pattern=r"^[A-Z_]{3,16}$")] = None,
    so_id: Annotated[int | None, Query(ge=1)] = None,
    po_id: Annotated[int | None, Query(ge=1)] = None,
    assignee_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="선적번호·거래 상대명 부분 일치")
    ] = None,
) -> dict[str, object]:
    return {
        "statuses": status,
        "shipment_kind": shipment_kind,
        "so_id": so_id,
        "po_id": po_id,
        "assignee_id": assignee_id,
        "q": q,
    }


Filters = Annotated[dict[str, object], Depends(_filters)]


@router.get("", summary="선적 목록 (페이지·필터 — 전 역할)")
def list_shipments(
    current: CurrentUser, params: Annotated[PageParams, Depends()], filters: Filters
) -> Page[ShipmentListItem]:
    items, total = shipment_view.list_shipments(
        offset=params.offset,
        limit=params.limit,
        **filters,  # type: ignore[arg-type]
    )
    return Page.of([shipment_list_item_out(item) for item in items], total, params)


@router.get(
    "/export.csv",
    summary="선적 목록 CSV 내보내기 (UTF-8 BOM·목록과 같은 필터 — 전 역할, 원가 열 0)",
)
def export_shipments_csv(current: CurrentUser, filters: Filters) -> StreamingResponse:
    rows = shipment_view.export_rows(**filters)  # type: ignore[arg-type]
    return csv_response("선적목록.csv", shipment_view.EXPORT_HEADER, rows)


# ★ `/{shipment_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 int 경로 검증이 먼저 잡아 422).
@router.get("/{shipment_id}", summary="선적 상세 (헤더·라인·당사자·가용 자리 — 전 역할)")
def get_shipment(shipment_id: Annotated[int, Path(ge=1)], current: CurrentUser) -> ShipmentDetail:
    return shipment_detail_out(shipment_view.get_shipment(shipment_id, current.roles))


@router.get("/{shipment_id}/status-log", summary="선적 상태 이력 (페이지 — 불변)")
def list_status_log(
    shipment_id: Annotated[int, Path(ge=1)],
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
) -> Page[StatusLogOut]:
    items, total = shipment_view.list_status_log(
        shipment_id=shipment_id, offset=params.offset, limit=params.limit
    )
    return Page.of([StatusLogOut.model_validate(item) for item in items], total, params)


# ── 헤더·라인 ─────────────────────────────────────────────────────────────────


@router.patch(
    "/{shipment_id}",
    summary="선적 헤더 편집 (메모·담당 = 상태 무관, 국가 = 계획 중에만 — 무역·물류)",
    dependencies=[require_roles(*CAN_OPERATE)],
)
def update_shipment(
    shipment_id: Annotated[int, Path(ge=1)], payload: ShipmentUpdateRequest, current: CurrentUser
) -> ShipmentDetail:
    body = shipment_flow.update_shipment(
        actor=current, shipment_id=shipment_id, payload=payload.model_dump(exclude_unset=True)
    )
    return shipment_detail_out(body)


@router.post(
    "/{shipment_id}/lines",
    summary="선적 라인 추가 (계획 중에만 — 원천 수주 라인 잔량 안에서)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def add_shipment_line(
    shipment_id: Annotated[int, Path(ge=1)],
    payload: ShipmentLineAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ShipmentDetail:
    status_code, body = shipment_flow.add_line(
        actor=current, idempotency_key=key, shipment_id=shipment_id, payload=payload.model_dump()
    )
    response.status_code = status_code
    return shipment_detail_out(body)


@router.patch(
    "/{shipment_id}/lines/{line_id}",
    summary="선적 라인 수량 수정 (계획 중에만 — 원천 잔량 + 현재 수량 안에서)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_shipment_line(
    shipment_id: Annotated[int, Path(ge=1)],
    line_id: Annotated[int, Path(ge=1)],
    payload: ShipmentLineUpdateRequest,
    current: CurrentUser,
) -> ShipmentDetail:
    body = shipment_flow.update_line(
        actor=current, shipment_id=shipment_id, line_id=line_id, payload=payload.model_dump()
    )
    return shipment_detail_out(body)


@router.delete(
    "/{shipment_id}/lines/{line_id}",
    summary="선적 라인 제외 (계획 중에만 — 마지막 라인은 409, 선적 취소로)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def remove_shipment_line(
    shipment_id: Annotated[int, Path(ge=1)],
    line_id: Annotated[int, Path(ge=1)],
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="헤더 version(낙관 잠금)")],
) -> ShipmentDetail:
    body = shipment_flow.remove_line(
        actor=current, shipment_id=shipment_id, line_id=line_id, version=version
    )
    return shipment_detail_out(body)


# ── 출고지시·취소 ──────────────────────────────────────────────────────────────


@router.post(
    "/{shipment_id}/release-order",
    summary="출고지시 (동결 액션 — 계획→출고지시, 이후 라인·국가 편집 불가. 무역·물류)",
    dependencies=[require_roles(*CAN_OPERATE)],
)
def release_shipment_order(
    shipment_id: Annotated[int, Path(ge=1)],
    payload: ShipmentVersionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ShipmentDetail:
    status_code, body = shipment_flow.release_shipment_order(
        actor=current, idempotency_key=key, shipment_id=shipment_id, version=payload.version
    )
    response.status_code = status_code
    return shipment_detail_out(body)


@router.post(
    "/{shipment_id}/transitions",
    summary="선적 취소 (사유 필수 — 마지막 살아 있는 선적이면 수주가 같은 TX에서 확정으로 복귀. 무역)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def transition_shipment(
    shipment_id: Annotated[int, Path(ge=1)],
    payload: ShipmentTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ShipmentDetail:
    status_code, body = shipment_flow.transition_shipment(
        actor=current,
        idempotency_key=key,
        shipment_id=shipment_id,
        to=payload.to_status,
        version=payload.version,
        reason=payload.reason,
    )
    response.status_code = status_code
    return shipment_detail_out(body)


# ── 당사자 ───────────────────────────────────────────────────────────────────


@router.post(
    "/{shipment_id}/parties",
    summary="선적 당사자 추가 (통지처·포워더·관세사 — 영문명 스냅샷, 역할당 1건. 무역·물류)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_OPERATE)],
)
def add_shipment_party(
    shipment_id: Annotated[int, Path(ge=1)],
    payload: PartyAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ShipmentDetail:
    status_code, body = shipment_flow.add_party(
        actor=current, idempotency_key=key, shipment_id=shipment_id, payload=payload.model_dump()
    )
    response.status_code = status_code
    return shipment_detail_out(body)


@router.delete(
    "/{shipment_id}/parties/{party_id}",
    summary="선적 당사자 제외 (자동 수하인은 불가 — 무역·물류)",
    dependencies=[require_roles(*CAN_OPERATE)],
)
def remove_shipment_party(
    shipment_id: Annotated[int, Path(ge=1)],
    party_id: Annotated[int, Path(ge=1)],
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="당사자 version(낙관 잠금)")],
) -> ShipmentDetail:
    body = shipment_flow.remove_party(
        actor=current, shipment_id=shipment_id, party_id=party_id, version=version
    )
    return shipment_detail_out(body)
