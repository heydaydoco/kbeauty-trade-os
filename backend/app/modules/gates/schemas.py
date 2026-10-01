"""게이트 API 스키마 (S3-1 PR-11a / design-D D3·D7). 쓰기 요청은 `extra=forbid`, 응답은 판매가·수량·준비 상태 요약만(원가·마진·매입가 필드 없음)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from app.modules.gates.text import REASON_MAX, reason_problem
from app.modules.gates.types import GateCode

GateCodeLiteral = Literal[
    "ITEM_MAPPING",
    "DUPLICATE_PO",
    "PRICE_DEVIATION",
    "CREDIT",
    "MARKET_READINESS",
    "MOQ",
    "PI_DEPOSIT",
]
if set(GateCodeLiteral.__args__) != {c.value for c in GateCode}:  # type: ignore[attr-defined]
    raise RuntimeError("GateCodeLiteral이 GateCode 열거와 다릅니다 — 스키마를 갱신하세요.")


class GateOverrideRequest(BaseModel):
    """override 부여·철회 본문 — 사람이 본 판정(`basis_hash`)과 사유에 결속한다. 확정 요청에 첨부하는 방식은 없다."""

    model_config = ConfigDict(extra="forbid")

    #: 7종 전부 받는다 — override 불가 게이트(품번·중복 PO·여신)는 조용한 400이 아니라 422 `NOT_APPLICABLE`로 거부한다.
    gate_code: GateCodeLiteral
    #: SO 라인 id — 게이트 단위 결과(예: PI 입금)는 생략.
    line_id: StrictInt | None = Field(default=None, ge=1)
    basis_hash: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    reason: StrictStr = Field(max_length=REASON_MAX * 2)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        problem = reason_problem(value)
        if problem is not None:
            raise ValueError(problem)
        return value.strip()


class GateOverrideInfo(BaseModel):
    id: int
    #: 자유 텍스트 — 무역·관리자에게만(그 외 역할은 null).
    reason: str | None
    authorized_role: str
    granted_by_id: int
    created_at: datetime


class GateResultOut(BaseModel):
    """게이트 결과 1건 — 서버가 계산한 값(화면은 산술하지 않는다)."""

    gate_code: str
    line_id: int | None
    line_no: int | None
    level: str
    resolution: str
    reason_code: str
    message_ko: str
    basis: dict[str, Any]
    #: 표시 전용 상세(판정 입력 아님·해시 불포함) — 다른 문서번호 등. 무역·관리자에게만.
    detail: dict[str, Any]
    #: 여신(CREDIT)은 마스킹 역할에게 빈 문자열(override 불가라 결속 값이 필요 없다).
    basis_hash: str
    #: 이 결과에 override를 부여할 수 있는 역할(해소가 OVERRIDE일 때만).
    override_roles: list[str]
    #: NOT_REQUIRED·OVERRIDDEN·APPROVED·UNRESOLVED — `clearance`의 정산 결과(화면이 통과를 다시 판정하지 않는다).
    settlement: str
    #: 지금 이 사용자가 override를 부여할 수 있는가(결과·역할·SO 상태 기준 — 서버가 계산).
    can_override: bool
    override: GateOverrideInfo | None


class ClearanceOut(BaseModel):
    cleared: bool
    needs_approval: bool
    unresolved_count: int


class ApprovalInfo(BaseModel):
    available: bool
    approval_id: int | None


class PolicyEffective(BaseModel):
    value: str | int
    #: SET(설정됨) 또는 UNSET_DEFAULT(미설정 — 가장 엄격한 기본 동작) — 조용한 기본값 금지.
    source: str


class GateReportOut(BaseModel):
    """`GET /sales-orders/{id}/gates` — 7종 평가 결과 + 확정 가능 판정. **조회는 아무것도 저장하지 않는다**(참고값 — 확정은 잠금 하 재평가)."""

    subject_type: str
    subject_id: int
    #: 평가 시점 SO 상태·판정 입력 digest(승인 결속 토큰과 같은 정의).
    status: str
    input_digest: str
    #: False — 잠금 없는 참고값(여신 포함). 확정 통로가 잠금 하에서 다시 평가한다.
    authoritative: bool
    evaluated_at: datetime
    #: 시장 준비도 결과는 계산값 표시이며 법적 판정이 아니다 — 고정 문구.
    note: str
    readiness_scope_note: str
    clearance: ClearanceOut
    approval: ApprovalInfo
    policies: dict[str, PolicyEffective]
    gates: list[GateResultOut]


class GateOverrideOut(BaseModel):
    id: int
    subject_type: str
    subject_id: int
    line_id: int | None
    gate_code: str
    action: str
    result_at_grant: str
    reason: str
    basis_hash: str
    granted_by_id: int
    authorized_role: str
    created_at: datetime
