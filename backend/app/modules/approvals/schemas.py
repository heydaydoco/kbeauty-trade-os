"""승인·결재선·대결 요청·응답 스키마 (S3-1 ADR-0060·0061 / design-C C3·C5·C8).

★ **요청 스키마는 전부 `extra="forbid"`**다 — 상태(status)·결재자(approver)·결정 컬럼(decided_*) 필드가 존재하지 않는다(상태는 `decide_approval`이,
  결재 역할은 결재선 매핑이 정한다). 승인 **요청 생성은 HTTP로 노출하지 않는다**(소비 전표 엔드포인트가 `request_approval`을 부른다 — 임의 대상·금액의
  승인 행을 위조할 표면을 없앤다).
★ 응답 스키마의 새 필드는 **기본값이 필수**다(멱등 재생이 저장된 옛 응답을 새 스키마로 검증할 때 500이 나지 않게 — R8).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

ApprovalTypeLiteral = Literal["SO_CREDIT_EXCEEDED"]
ApproverRoleLiteral = Literal["ADMIN", "TRADE", "LOGISTICS", "CERT"]
StatusLiteral = Literal["REQUESTED", "APPROVED", "REJECTED", "WITHDRAWN", "CONSUMED", "VOIDED"]


# ── 결재선 ───────────────────────────────────────────────────────────────────


class ApprovalLineCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_type: ApprovalTypeLiteral
    #: 사람 표기 금액(임계 — 이 금액을 **초과**하면 아래 역할이 결재한다). 서비스가 정수 최소단위로 변환한다.
    threshold: Decimal = Field(ge=0, max_digits=15)
    currency: StrictStr = Field(min_length=3, max_length=3)
    approver_role: ApproverRoleLiteral
    note: StrictStr | None = Field(default=None, max_length=200)


class ApprovalLineUpdateRequest(BaseModel):
    """보낸 필드만 바뀐다 — 역할·메모뿐(유형·통화·임계는 삭제 후 신규 등록)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    approver_role: ApproverRoleLiteral | None = None
    note: StrictStr | None = Field(default=None, max_length=200)


class ApprovalLineOut(BaseModel):
    id: int
    approval_type: str
    threshold_amount: int
    threshold_currency: str
    threshold_text: str = ""
    approver_role: str
    note: str | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CoverageEntry(BaseModel):
    approval_type: str
    currency: str
    lines: int = 0
    has_zero_threshold: bool = False


class CoverageOut(BaseModel):
    configured: bool = False
    entries: list[CoverageEntry] = []
    messages: list[str] = []


# ── 승인 ─────────────────────────────────────────────────────────────────────


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verb: Literal["APPROVE", "REJECT", "WITHDRAW"]
    reason: StrictStr | None = Field(default=None, max_length=1000)
    version: StrictInt = Field(ge=1)


class ApprovalView(BaseModel):
    id: int
    approval_type: str
    target_type: str
    target_id: int
    target_label: str
    status: str
    required_role: str
    requested_by_id: int | None = None
    requester_name: str | None = None
    basis_amount: int
    basis_currency: str
    snapshot: dict[str, Any] = {}
    decided_by_name: str | None = None
    decided_on_behalf_of_name: str | None = None
    decided_at: datetime | None = None
    consumed_at: datetime | None = None
    status_reason: str | None = None
    void_reason_code: str | None = None
    #: 서버 계산 — 화면이 역할로 추정하지 않는다(ADMIN은 hasRole이 항상 true라 기안자=승인자 버튼이 상시 노출된다).
    can_decide: bool = False
    decide_blocked_reason: Literal["SELF", "NOT_APPROVER"] | None = None
    can_withdraw: bool = False
    created_at: datetime | None = None
    version: int


class ApprovalEventOut(BaseModel):
    id: int
    occurred_at: datetime
    from_status: str | None = None
    to_status: str
    actor_name: str | None = None
    on_behalf_of_name: str | None = None
    reason: str | None = None
    reason_code: str | None = None


class InboxCountOut(BaseModel):
    count: int = 0


# ── 대결 ─────────────────────────────────────────────────────────────────────


class DelegationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delegate_user_id: StrictInt = Field(ge=1)
    approval_type: ApprovalTypeLiteral
    delegated_role: ApproverRoleLiteral
    #: KST 달력 날짜(양끝 포함). 소급(오늘 이전 시작)은 422.
    start_on: date
    end_on: date
    note: StrictStr | None = Field(default=None, max_length=200)
    #: ADMIN만 — 부재자 대신 등록(미지정이면 본인). 비ADMIN이 타인을 지정하면 403.
    delegator_user_id: StrictInt | None = Field(default=None, ge=1)


class DelegationRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class DelegationOut(BaseModel):
    id: int
    delegator_user_id: int
    delegator_name: str | None = None
    delegate_user_id: int
    delegate_name: str | None = None
    approval_type: str
    delegated_role: str
    start_on: date
    end_on: date
    note: str | None = None
    #: ACTIVE·UPCOMING·EXPIRED·REVOKED·INERT(계산 상태 — 위임자·수임자 비활성·역할 상실)
    state: str = "ACTIVE"
    inert_reason: str | None = None
    revoked_at: datetime | None = None
    can_revoke: bool = False
    version: int
    created_at: datetime | None = None


class CandidateOut(BaseModel):
    """수임자 후보 — **두 키뿐**(이메일·역할·비활성 사유를 싣지 않는다)."""

    id: int
    display_name: str
