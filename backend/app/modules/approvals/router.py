"""승인·결재선·대결 엔드포인트 (S3-1 ADR-0060·0061 / design-C C8).

권한(서버측 역할+소유권 — ADMIN은 `require_roles`를 통과하되 **직무분리·결재 자격은 서비스가 따로 판정**한다):
  · 결재선 조회·커버리지: ADMIN·TRADE·LOGISTICS·CERT / 쓰기: ADMIN 전용
  · 승인 목록(inbox=결재 자격자만·mine=본인 기안·all=ADMIN)·배지·후보·상세·이력·결정: 비조회 4역할(VIEWER 전면 403) + 서비스 자격 판정
  · 대결: 비조회 4역할(전체 목록 scope=all은 ADMIN)
**`POST /approvals`(승인 요청 생성)는 존재하지 않는다** — 소비 전표 엔드포인트가 `request_approval`을 호출한다.
정적 경로(`inbox-count`·`delegation-candidates`)는 `/{id}`보다 **먼저** 선언한다(int 경로 파라미터 충돌 방지).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.approvals import delegations, lines, service, views
from app.modules.approvals.machine import DecisionVerb
from app.modules.approvals.schemas import (
    ApprovalEventOut,
    ApprovalLineCreateRequest,
    ApprovalLineOut,
    ApprovalLineUpdateRequest,
    ApprovalView,
    CandidateOut,
    CoverageOut,
    DecisionRequest,
    DelegationCreateRequest,
    DelegationOut,
    DelegationRevokeRequest,
    InboxCountOut,
    StatusLiteral,
)
from app.modules.identity.models import RoleCode

#: 비조회 4역할 — VIEWER는 승인 API 전면 403(여신 초과액 노출 재판정을 열지 않는 가장 좁은 결정). 관리자는 상시 통과.
CAN_ACT = (RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT)
ADMIN_ONLY = require_roles()

lines_router = APIRouter(prefix="/approval-lines", tags=["approvals"])
router = APIRouter(prefix="/approvals", tags=["approvals"])
delegations_router = APIRouter(prefix="/delegations", tags=["approvals"])


# ── 결재선 ───────────────────────────────────────────────────────────────────


@lines_router.get(
    "", summary="결재선 목록 (비조회 4역할 — 페이지)", dependencies=[require_roles(*CAN_ACT)]
)
def list_approval_lines(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    approval_type: Annotated[str | None, Query(max_length=30)] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
) -> Page[ApprovalLineOut]:
    items, total = lines.list_approval_lines(
        offset=params.offset, limit=params.limit, approval_type=approval_type, currency=currency
    )
    return Page.of([ApprovalLineOut.model_validate(i) for i in items], total, params)


@lines_router.get(
    "/coverage",
    summary="결재선 공급 현황 — 미등록이면 여신 초과 수주 확정 불가(정상 fail-closed) 안내",
    dependencies=[require_roles(*CAN_ACT)],
)
def approval_line_coverage(current: CurrentUser) -> CoverageOut:
    return CoverageOut.model_validate(lines.coverage())


@lines_router.post(
    "",
    summary="결재선 등록 (관리자 전용 — 임계 초과 시 이 역할이 결재)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[ADMIN_ONLY],
)
def create_approval_line(
    payload: ApprovalLineCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ApprovalLineOut:
    status_code, body = lines.create_approval_line(
        actor=current, idempotency_key=key, payload=payload.model_dump()
    )
    response.status_code = status_code
    return ApprovalLineOut.model_validate(body)


@lines_router.patch(
    "/{line_id}",
    summary="결재선 수정 (관리자 전용 — 역할·메모만, 이미 요청된 승인에는 소급하지 않음)",
    dependencies=[ADMIN_ONLY],
)
def update_approval_line(
    line_id: int, payload: ApprovalLineUpdateRequest, current: CurrentUser
) -> ApprovalLineOut:
    body = lines.update_approval_line(
        actor=current, line_id=line_id, payload=payload.model_dump(exclude_unset=True)
    )
    return ApprovalLineOut.model_validate(body)


@lines_router.delete(
    "/{line_id}",
    summary="결재선 삭제 (관리자 전용 — soft delete)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[ADMIN_ONLY],
)
def delete_approval_line(
    line_id: int,
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="화면이 본 version(낙관 잠금)")],
) -> Response:
    lines.delete_approval_line(actor=current, line_id=line_id, version=version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── 승인 ─────────────────────────────────────────────────────────────────────


@router.get("", summary="승인 목록 (결재함·내 요청·전체)", dependencies=[require_roles(*CAN_ACT)])
def list_approvals(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    scope: Annotated[Literal["inbox", "mine", "all"], Query()] = "inbox",
    status: Annotated[StatusLiteral | None, Query()] = None,
) -> Page[ApprovalView]:
    items, total = views.list_approvals(
        actor=current, scope=scope, status=status, offset=params.offset, limit=params.limit
    )
    return Page.of([ApprovalView.model_validate(i) for i in items], total, params)


@router.get(
    "/inbox-count",
    summary="결재함 배지 — 내가 결정할 수 있는 대기 건수(알림과 무관한 서버 계산)",
    dependencies=[require_roles(*CAN_ACT)],
)
def approval_inbox_count(current: CurrentUser) -> InboxCountOut:
    return InboxCountOut(count=views.inbox_count(actor=current))


@router.get(
    "/delegation-candidates",
    summary="대결 수임자 후보 (활성·비조회 역할 보유자 — id·표시명만)",
    dependencies=[require_roles(*CAN_ACT)],
)
def delegation_candidates(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> Page[CandidateOut]:
    items, total = delegations.list_candidates(
        actor=current, q=q, offset=params.offset, limit=params.limit
    )
    return Page.of([CandidateOut.model_validate(i) for i in items], total, params)


@router.get(
    "/{approval_id}",
    summary="승인 상세 (기안자·결정자·결재 자격자·관리자만)",
    dependencies=[require_roles(*CAN_ACT)],
)
def get_approval(approval_id: int, current: CurrentUser) -> ApprovalView:
    return ApprovalView.model_validate(views.get_approval(actor=current, approval_id=approval_id))


@router.get(
    "/{approval_id}/events",
    summary="승인 이력 (상태 변경 — 행위자·대결·사유)",
    dependencies=[require_roles(*CAN_ACT)],
)
def list_approval_events(
    approval_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ApprovalEventOut]:
    items, total = views.list_events(
        actor=current, approval_id=approval_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ApprovalEventOut.model_validate(i) for i in items], total, params)


@router.post(
    "/{approval_id}/decisions",
    summary="승인·반려·회수 결정 (결재 자격 판정은 서비스 — 기안자 본인은 ADMIN 포함 403)",
    dependencies=[require_roles(*CAN_ACT)],
)
def decide_approval(
    approval_id: int, payload: DecisionRequest, current: CurrentUser, key: IdempotencyKey
) -> ApprovalView:
    _, body = service.decide_approval(
        approval_id=approval_id,
        actor=current,
        verb=DecisionVerb(payload.verb),
        reason=payload.reason,
        version=payload.version,
        idempotency_key=key,
    )
    return ApprovalView.model_validate(body)


# ── 대결 ─────────────────────────────────────────────────────────────────────


@delegations_router.get(
    "",
    summary="대결 목록 (mine=내가 위임·수임한 건, all=관리자)",
    dependencies=[require_roles(*CAN_ACT)],
)
def list_delegations(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    scope: Annotated[Literal["mine", "all"], Query()] = "mine",
) -> Page[DelegationOut]:
    items, total = delegations.list_delegations(
        actor=current, scope=scope, offset=params.offset, limit=params.limit
    )
    return Page.of([DelegationOut.model_validate(i) for i in items], total, params)


@delegations_router.post(
    "",
    summary="대결 등록 (위임자 본인 또는 관리자 — 소급 금지·기간 겹침 금지)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_ACT)],
)
def create_delegation(
    payload: DelegationCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> DelegationOut:
    status_code, body = delegations.create_delegation(
        actor=current, idempotency_key=key, payload=payload.model_dump()
    )
    response.status_code = status_code
    return DelegationOut.model_validate(body)


@delegations_router.post(
    "/{delegation_id}/revoke",
    summary="대결 조기 종료 (위임자 본인 또는 관리자)",
    dependencies=[require_roles(*CAN_ACT)],
)
def revoke_delegation(
    delegation_id: int, payload: DelegationRevokeRequest, current: CurrentUser
) -> DelegationOut:
    body = delegations.revoke_delegation(
        actor=current, delegation_id=delegation_id, version=payload.version
    )
    return DelegationOut.model_validate(body)
