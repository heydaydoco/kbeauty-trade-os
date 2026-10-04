"""선적 마일스톤·변경 이력·통보·통관 기록 엔드포인트 (S3-2 PR-4a / design-D D2-1 S16~S19·D2-2 M1~M6 / ADR-0079·0080·0083).

권한은 **경로 단위**다(라우터 가드만으로 403이 존재 검사 전에 성립 — 401→403→404→409→422, `D:370`):
  조회(보드·변경 이력·통관 목록) = 전 역할 / 계획·실적·초안·통보·통관 쓰기 = 무역 + **물류**(일정·실적·통관은 물류 실무 — ADR-0079).
  관리자는 `require_roles`에서 상시 통과한다.
  **PR-4c**: OEM 생산 일정(`/purchase-orders/{po_id}/milestones…` M7~M9) — 조회 = 전 역할(원가 키 없음), 계획·실적 = **무역**(PO는 무역 소관 —
  물류의 PO 쓰기 0, X-16·ADR-0079 ④).
쓰기는 전부 사람 1클릭이다. 계획·실적·초안·통보·통관 추가는 `Idempotency-Key` 필수, 통관 정정·삭제는 `version` 필수(409).
경로의 `{milestone_type}`은 종류 전체(파생 포함)를 받는다 — 파생 3종은 스키마 422가 아니라 도메인 422 `DERIVED_NOT_EDITABLE`(덮어쓰기 금지 —
design-D D2-2). 이 라우터가 마일스톤·통관 쓰기 함수의 **유일한 호출처**다(자동 일정·자동 통관 0 — test_no_auto_confirm_code_path_exists).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.shipments.schemas import (
    CustomsRecordCreateRequest,
    CustomsRecordDeleteRequest,
    CustomsRecordOut,
    CustomsRecordUpdateRequest,
    MilestoneActualRequest,
    MilestoneBoardOut,
    MilestoneChangeOut,
    MilestoneNoticeRequest,
    MilestonePlanDraftRequest,
    MilestonePlanRequest,
    MilestoneWriteOut,
    OemMilestoneBoardOut,
    OemMilestoneWriteOut,
)
from app.modules.trade_chain import customs_flow, milestone_flow, milestone_view
from app.modules.trade_docs.constants import MilestoneChangeKind, MilestoneType

#: 일정·실적·통보·통관 — 무역 + 물류(관리자 상시 통과).
CAN_RECORD = (RoleCode.TRADE, RoleCode.LOGISTICS)
#: OEM 생산 일정(PO 소유) — 무역(관리자 상시 통과). 물류의 PO 쓰기 0(X-16).
CAN_RECORD_OEM = (RoleCode.TRADE,)

ChangeKindFilter = Literal["PLAN_SET", "PLAN_CHANGED", "ACTUAL_RECORDED", "ACTUAL_CORRECTED"]
assert set(ChangeKindFilter.__args__) == {k.value for k in MilestoneChangeKind}  # type: ignore[attr-defined]

router = APIRouter(prefix="/shipments", tags=["shipments"])
#: PR-4c — OEM 생산 일정(PO 하위 경로 — `/api/v1/purchase-orders` 통제 접두어가 행 누락을 잡는다).
oem_router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])

ShipmentId = Annotated[int, Path(ge=1)]
PoId = Annotated[int, Path(ge=1)]


# ── 마일스톤 보드·계획·실적·초안 ──────────────────────────────────────────────────


@router.get(
    "/{shipment_id}/milestones",
    summary="선적 마일스톤 보드 (계획·실적·파생 3종·휴일 경고·통관 수리 상태 — 전 역할)",
)
def get_shipment_milestones(shipment_id: ShipmentId, current: CurrentUser) -> MilestoneBoardOut:
    return MilestoneBoardOut.model_validate(milestone_view.get_board(shipment_id))


@router.post(
    "/{shipment_id}/milestones/plan-draft",
    summary="마일스톤 계획 초안 (사람 1클릭 — 적용 종류의 빈 계획 행, 있는 종류는 건너뜀. 무역·물류)",
    dependencies=[require_roles(*CAN_RECORD)],
)
def draft_shipment_milestones(
    shipment_id: ShipmentId,
    payload: MilestonePlanDraftRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> MilestoneBoardOut:
    status_code, body = milestone_flow.draft_milestone_plan(
        actor=current, idempotency_key=key, shipment_id=shipment_id
    )
    response.status_code = status_code
    return MilestoneBoardOut.model_validate(body)


@router.post(
    "/{shipment_id}/milestones/{milestone_type}/plan",
    summary="마일스톤 계획 설정·변경 (변경 = 롤오버 — 사유 필수. 파생 종류 422. 무역·물류)",
    dependencies=[require_roles(*CAN_RECORD)],
)
def plan_shipment_milestone(
    shipment_id: ShipmentId,
    milestone_type: MilestoneType,
    payload: MilestonePlanRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> MilestoneWriteOut:
    status_code, body = milestone_flow.record_milestone_plan(
        actor=current,
        idempotency_key=key,
        shipment_id=shipment_id,
        milestone_type=milestone_type.value,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return MilestoneWriteOut.model_validate(body)


@router.post(
    "/{shipment_id}/milestones/{milestone_type}/actual",
    summary="마일스톤 실적 기록·정정 (ETD·B/L·ETA는 출고지시 뒤만, 정정 = 사유 필수, 신고수리는 통관 기록에서. 무역·물류)",
    dependencies=[require_roles(*CAN_RECORD)],
)
def record_shipment_milestone_actual(
    shipment_id: ShipmentId,
    milestone_type: MilestoneType,
    payload: MilestoneActualRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> MilestoneWriteOut:
    status_code, body = milestone_flow.record_milestone_actual(
        actor=current,
        idempotency_key=key,
        shipment_id=shipment_id,
        milestone_type=milestone_type.value,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return MilestoneWriteOut.model_validate(body)


# ── 변경 이력·통보 ─────────────────────────────────────────────────────────────


@router.get(
    "/{shipment_id}/milestone-changes",
    summary="마일스톤 변경 이력 (롤오버·실적 정정 — 불변, 통보 기록 내장. 페이지 — 전 역할)",
)
def list_shipment_milestone_changes(
    shipment_id: ShipmentId,
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    milestone_type: Annotated[MilestoneType | None, Query()] = None,
    change_kind: Annotated[ChangeKindFilter | None, Query()] = None,
) -> Page[MilestoneChangeOut]:
    items, total = milestone_view.list_changes(
        shipment_id=shipment_id,
        milestone_type=milestone_type.value if milestone_type is not None else None,
        change_kind=change_kind,
        offset=params.offset,
        limit=params.limit,
    )
    return Page.of([MilestoneChangeOut.model_validate(item) for item in items], total, params)


@router.post(
    "/{shipment_id}/milestone-changes/{change_id}/notices",
    summary="롤오버 통보 기록 (실제로 알린 사실의 기록 — 발송 0. 무역·물류)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_RECORD)],
)
def record_shipment_milestone_notice(
    shipment_id: ShipmentId,
    change_id: Annotated[int, Path(ge=1)],
    payload: MilestoneNoticeRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> MilestoneChangeOut:
    status_code, body = milestone_flow.record_milestone_notice(
        actor=current,
        idempotency_key=key,
        shipment_id=shipment_id,
        change_id=change_id,
        payload=payload.model_dump(),
    )
    response.status_code = status_code
    return MilestoneChangeOut.model_validate(body)


# ── 통관 기록 ────────────────────────────────────────────────────────────────


@router.get(
    "/{shipment_id}/customs-records",
    summary="통관 기록 목록 (사실 기록 — 세율·세액 없음. 페이지 — 전 역할)",
)
def list_shipment_customs_records(
    shipment_id: ShipmentId, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[CustomsRecordOut]:
    items, total = milestone_view.list_customs_records(
        shipment_id=shipment_id, offset=params.offset, limit=params.limit
    )
    return Page.of([CustomsRecordOut.model_validate(item) for item in items], total, params)


@router.post(
    "/{shipment_id}/customs-records",
    summary="통관 기록 추가 (신고 구분 = 선적 구분, 신고일·수리일 ≤ 오늘. 무역·물류)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_RECORD)],
)
def create_shipment_customs_record(
    shipment_id: ShipmentId,
    payload: CustomsRecordCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> CustomsRecordOut:
    status_code, body = customs_flow.create_customs_record(
        actor=current, idempotency_key=key, shipment_id=shipment_id, payload=payload.model_dump()
    )
    response.status_code = status_code
    return CustomsRecordOut.model_validate(body)


@router.patch(
    "/{shipment_id}/customs-records/{record_id}",
    summary="통관 기록 정정 (신고번호·신고일·기존 수리일 변경은 사유 필수 — audit. 무역·물류)",
    dependencies=[require_roles(*CAN_RECORD)],
)
def update_shipment_customs_record(
    shipment_id: ShipmentId,
    record_id: Annotated[int, Path(ge=1)],
    payload: CustomsRecordUpdateRequest,
    current: CurrentUser,
) -> CustomsRecordOut:
    body = customs_flow.update_customs_record(
        actor=current,
        shipment_id=shipment_id,
        record_id=record_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return CustomsRecordOut.model_validate(body)


@router.delete(
    "/{shipment_id}/customs-records/{record_id}",
    summary="통관 기록 삭제 (사유 필수 — audit, 선적 취소 전 탈출로. 무역·물류)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_RECORD)],
)
def delete_shipment_customs_record(
    shipment_id: ShipmentId,
    record_id: Annotated[int, Path(ge=1)],
    payload: CustomsRecordDeleteRequest,
    current: CurrentUser,
) -> Response:
    customs_flow.delete_customs_record(
        actor=current, shipment_id=shipment_id, record_id=record_id, payload=payload.model_dump()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── OEM 생산 일정 (M7~M9 — PO 소유 4종, PR-4c) ──────────────────────────────────────


@oem_router.get(
    "/{po_id}/milestones",
    summary="OEM 생산 일정 보드 (원료수급·충진·포장·출하검사 — 일반 구매 발주는 422. 전 역할, 원가 없음)",
)
def get_oem_milestones(po_id: PoId, current: CurrentUser) -> OemMilestoneBoardOut:
    return OemMilestoneBoardOut.model_validate(milestone_view.get_oem_board(po_id, current.roles))


@oem_router.post(
    "/{po_id}/milestones/{milestone_type}/plan",
    summary="OEM 생산 일정 계획 설정·변경 (변경 = 롤오버 — 사유 필수. OEM 4종만. 무역)",
    dependencies=[require_roles(*CAN_RECORD_OEM)],
)
def plan_oem_milestone(
    po_id: PoId,
    milestone_type: MilestoneType,
    payload: MilestonePlanRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> OemMilestoneWriteOut:
    status_code, body = milestone_flow.record_oem_milestone_plan(
        actor=current,
        idempotency_key=key,
        po_id=po_id,
        milestone_type=milestone_type.value,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return OemMilestoneWriteOut.model_validate(body)


@oem_router.post(
    "/{po_id}/milestones/{milestone_type}/actual",
    summary="OEM 생산 일정 실적 기록·정정 (정정 = 사유 필수, 미래 날짜 불가. OEM 4종만. 무역)",
    dependencies=[require_roles(*CAN_RECORD_OEM)],
)
def record_oem_milestone_actual(
    po_id: PoId,
    milestone_type: MilestoneType,
    payload: MilestoneActualRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> OemMilestoneWriteOut:
    status_code, body = milestone_flow.record_oem_milestone_actual(
        actor=current,
        idempotency_key=key,
        po_id=po_id,
        milestone_type=milestone_type.value,
        payload=payload.model_dump(exclude_unset=True),
    )
    response.status_code = status_code
    return OemMilestoneWriteOut.model_validate(body)


@oem_router.get(
    "/{po_id}/milestone-changes",
    summary="OEM 생산 일정 변경 이력 (롤오버·실적 정정 — 불변. 페이지 — 전 역할)",
)
def list_oem_milestone_changes(
    po_id: PoId,
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    milestone_type: Annotated[MilestoneType | None, Query()] = None,
    change_kind: Annotated[ChangeKindFilter | None, Query()] = None,
) -> Page[MilestoneChangeOut]:
    items, total = milestone_view.list_po_changes(
        po_id=po_id,
        milestone_type=milestone_type.value if milestone_type is not None else None,
        change_kind=change_kind,
        offset=params.offset,
        limit=params.limit,
    )
    return Page.of([MilestoneChangeOut.model_validate(item) for item in items], total, params)
