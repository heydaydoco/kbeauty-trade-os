"""대행 협업 엔드포인트 — 대행 계약·통신 기록 (§5.4 / §18.4 페이지네이션).

권한(§2 계보 — 인증 인스턴스와 같다): 편집 계열(등록·수정·삭제)=**인증**(관리자 상시 통과),
무역·물류·조회=열람. 계약 수수료는 원가·마진이 아니라 마스킹 비대상이다(여신한도 선례).
통신 기록의 첨부는 문서 보관소 API(소유 유형 COMM_LOG)로 붙인다 — 이 라우터에 파일 처리는 없다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.collaboration import service
from app.modules.collaboration.schemas import (
    CommLogCreateRequest,
    CommLogSummary,
    CommLogUpdateRequest,
    ContractCreateRequest,
    ContractSummary,
    ContractUpdateRequest,
    SubjectType,
)
from app.modules.identity.models import RoleCode

#: 대행 협업 편집은 인증 역할이 한다(관리자는 항상 통과).
CAN_EDIT = (RoleCode.CERT,)

contracts_router = APIRouter(prefix="/agency-contracts", tags=["agency-contracts"])
comm_logs_router = APIRouter(prefix="/comm-logs", tags=["comm-logs"])


# ── 대행 계약 ──────────────────────────────────────────────────────────────


@contracts_router.post(
    "",
    summary="대행 계약 등록 (대행사는 인증대행 유형 거래처만)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def create_contract(
    payload: ContractCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ContractSummary:
    status_code, body = service.create_contract(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return ContractSummary.model_validate(body)


@contracts_router.get("", summary="대행 계약 목록")
def list_contracts(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    partner_id: Annotated[int | None, Query(ge=1)] = None,
    current_only: Annotated[bool, Query(description="오늘(KST)이 계약 기간 안인 계약만")] = False,
) -> Page[ContractSummary]:
    views, total = service.list_contracts(
        partner_id=partner_id, current_only=current_only, offset=params.offset, limit=params.limit
    )
    return Page.of([ContractSummary.of(view) for view in views], total, params)


@contracts_router.get("/{contract_id}", summary="대행 계약 상세")
def get_contract(contract_id: int, current: CurrentUser) -> ContractSummary:
    return ContractSummary.of(service.get_contract(contract_id))


@contracts_router.patch(
    "/{contract_id}",
    summary="대행 계약 편집 (대행사는 못 바꿈 — 낙관 잠금 version 필수)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_contract(
    contract_id: int, payload: ContractUpdateRequest, current: CurrentUser
) -> ContractSummary:
    view = service.update_contract(
        actor=current,
        contract_id=contract_id,
        payload=payload.model_dump(mode="json", exclude_unset=True),
    )
    return ContractSummary.of(view)


@contracts_router.delete(
    "/{contract_id}",
    summary="대행 계약 삭제 (soft delete — 같은 번호 재등록은 신규)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_EDIT)],
)
def delete_contract(contract_id: int, current: CurrentUser) -> None:
    service.delete_contract(actor=current, contract_id=contract_id)


# ── 통신 기록 ──────────────────────────────────────────────────────────────


@comm_logs_router.post(
    "",
    summary="통신 기록 등록 (일어난 일의 기록 — 발송은 사람이 한다)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def create_comm_log(
    payload: CommLogCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> CommLogSummary:
    status_code, body = service.create_comm_log(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return CommLogSummary.model_validate(body)


@comm_logs_router.get("", summary="통신 기록 목록 (최근 오간 날 순)")
def list_comm_logs(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    subject_type: Annotated[SubjectType | None, Query()] = None,
    subject_id: Annotated[int | None, Query(ge=1)] = None,
    open_only: Annotated[bool, Query(description="완료되지 않은 다음 액션이 있는 기록만")] = False,
) -> Page[CommLogSummary]:
    views, total = service.list_comm_logs(
        subject_type=subject_type,
        subject_id=subject_id,
        open_only=open_only,
        offset=params.offset,
        limit=params.limit,
    )
    return Page.of([CommLogSummary.of(view) for view in views], total, params)


@comm_logs_router.get("/{log_id}", summary="통신 기록 상세")
def get_comm_log(log_id: int, current: CurrentUser) -> CommLogSummary:
    return CommLogSummary.of(service.get_comm_log(log_id))


@comm_logs_router.patch(
    "/{log_id}",
    summary="통신 기록 편집·다음 액션 완료 처리 (낙관 잠금 version 필수)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_comm_log(
    log_id: int, payload: CommLogUpdateRequest, current: CurrentUser
) -> CommLogSummary:
    view = service.update_comm_log(
        actor=current,
        log_id=log_id,
        payload=payload.model_dump(mode="json", exclude_unset=True),
    )
    return CommLogSummary.of(view)


@comm_logs_router.delete(
    "/{log_id}",
    summary="통신 기록 삭제 (soft delete)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_EDIT)],
)
def delete_comm_log(log_id: int, current: CurrentUser) -> None:
    service.delete_comm_log(actor=current, log_id=log_id)
