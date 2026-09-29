"""대행 계약·통신 기록·스코어카드 요청·응답 (§5.4 / S2-4 PR-1).

★ 쓰기 스키마는 전부 extra="forbid" — 모르는 필드(오타)는 조용한 무시가 아니라 422다.

★ 수정(PATCH)은 exclude_unset 의미론이다 — 안 보낸 필드는 안 바뀐다. 비울 수 없는
  필드(계약 번호·시작일·오간 날·요지)에 명시 null을 보내면 422다.

★ 수수료는 요청에서 사람이 쓰는 표기(12.34)로 받고, 응답은 정수 최소단위(`fee_amount`)다
  (금액 규약 — ADR-0003 ④, partners.credit_limit과 같은 꼴).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.pagination import Page
from app.modules.collaboration.scorecard import AgencyScoreView, CurrentContractView
from app.modules.collaboration.service import CommLogView, ContractView

SubjectType = Literal["CERTIFICATION"]


def _reject_null(model: BaseModel, names: tuple[str, ...]) -> None:
    for name in names:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name}은(는) 비울 수 없습니다.")


# ── 대행 계약 ──────────────────────────────────────────────────────────────


class ContractCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: 대행사 — 거래처 유형 CERT_AGENCY(서비스 검증).
    partner_id: int = Field(ge=1)
    contract_no: str = Field(min_length=1, max_length=60)
    scope_note: str | None = Field(default=None, max_length=2000)
    start_on: date
    #: 비우면 기간 미정(상시 계약).
    end_on: date | None = None
    #: 수수료 — 사람이 쓰는 표기. 통화와 한 쌍(둘 다 비우면 미기재).
    fee: Decimal | None = Field(default=None, ge=0, max_digits=15)
    fee_currency: str | None = Field(default=None, min_length=3, max_length=3)
    note: str | None = Field(default=None, max_length=2000)


class ContractUpdateRequest(BaseModel):
    """대행사(partner_id)는 바꿀 수 없다 — 계약의 정체성이다(다른 대행사는 새 계약)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    contract_no: str | None = Field(default=None, min_length=1, max_length=60)
    scope_note: str | None = Field(default=None, max_length=2000)
    start_on: date | None = None
    end_on: date | None = None
    fee: Decimal | None = Field(default=None, ge=0, max_digits=15)
    fee_currency: str | None = Field(default=None, min_length=3, max_length=3)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _non_nullable(self) -> ContractUpdateRequest:
        _reject_null(self, ("contract_no", "start_on"))
        return self


class ContractSummary(BaseModel):
    id: int
    partner_id: int
    partner_name: str
    contract_no: str
    scope_note: str | None
    start_on: date
    end_on: date | None
    #: 정수 최소단위 — 표시 변환은 통화별 자릿수(서버 제공)로만 한다.
    fee_amount: int | None
    fee_currency: str | None
    note: str | None
    version: int
    #: 계산값(저장 아님) — 오늘(KST)이 계약 기간 안인가.
    is_current: bool

    @classmethod
    def of(cls, view: ContractView) -> ContractSummary:
        return cls(**asdict(view))


# ── 통신 기록 ──────────────────────────────────────────────────────────────


class CommLogCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: SubjectType
    subject_id: int = Field(ge=1)
    #: 상대 거래처(선택) — 기관 담당자 등 거래처가 아닌 상대는 요지에 적는다.
    partner_id: int | None = Field(default=None, ge=1)
    #: 실제로 오간 날(KST 업무일) — 입력일과 다를 수 있다(소급 입력).
    occurred_on: date
    summary: str = Field(min_length=1, max_length=2000)
    next_action: str | None = Field(default=None, max_length=500)
    next_action_due: date | None = None


class CommLogUpdateRequest(BaseModel):
    """다음 액션 내용을 null로 비우면 기한·완료일도 함께 비워진다.

    완료 처리 = next_action_done_on에 날짜, 완료 취소(재개) = null.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    partner_id: int | None = Field(default=None, ge=1)
    occurred_on: date | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=2000)
    next_action: str | None = Field(default=None, max_length=500)
    next_action_due: date | None = None
    next_action_done_on: date | None = None

    @model_validator(mode="after")
    def _non_nullable(self) -> CommLogUpdateRequest:
        _reject_null(self, ("occurred_on", "summary"))
        return self


class CommLogSummary(BaseModel):
    id: int
    subject_type: str
    subject_id: int
    subject_label: str
    partner_id: int | None
    partner_name: str | None
    occurred_on: date
    summary: str
    next_action: str | None
    next_action_due: date | None
    next_action_done_on: date | None
    #: 계산값 — 다음 액션이 남아 있는가 / 기한이 지났는가(KST 오늘 기준).
    follow_up_open: bool
    follow_up_overdue: bool
    attachment_count: int
    created_at: str
    version: int

    @classmethod
    def of(cls, view: CommLogView) -> CommLogSummary:
        return cls(**asdict(view))


# ── 스코어카드 ─────────────────────────────────────────────────────────────


class CurrentContractSummary(BaseModel):
    id: int
    contract_no: str
    start_on: date
    end_on: date | None
    #: 정수 최소단위 — 합산·환산하지 않고 계약에 적힌 그대로 보인다.
    fee_amount: int | None
    fee_currency: str | None
    scope_note: str | None

    @classmethod
    def of(cls, view: CurrentContractView) -> CurrentContractSummary:
        return cls(**asdict(view))


class AgencyScore(BaseModel):
    partner_id: int
    partner_code: str
    partner_name: str
    #: 삭제되지 않은 인증 전건(종결 포함) — 현재 대행사 귀속.
    case_count: int
    submitted_count: int
    approved_count: int
    supplemented_count: int
    #: 보완요청을 거친 건 ÷ 신청제출 도달 건(0~1) — 분모 0이면 null(0%로 위장 금지).
    supplement_rate: float | None
    #: 소요일 표본 수(승인에 도달한 건).
    lead_sample_count: int
    lead_days_avg: float | None
    lead_days_median: float | None
    current_contract: CurrentContractSummary | None

    @classmethod
    def of(cls, view: AgencyScoreView) -> AgencyScore:
        data = asdict(view)
        contract = view.current_contract
        data["current_contract"] = (
            CurrentContractSummary.of(contract) if contract is not None else None
        )
        return cls(**data)


class ScorecardPage(Page[AgencyScore]):
    """페이지 봉투 + 지표 정의 문구(서버가 정본 — 화면은 그대로 보인다)."""

    note: str
