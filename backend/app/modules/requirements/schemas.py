"""요건 템플릿 요청·응답 (§5.1 / S2-1 PR-2).

★ status는 생성·편집 요청에 CONFIRMED가 올 수 없다 — 확정 진입은 확정 액션
  하나뿐이다(ADR-0033). 생성은 항상 DRAFT로 시작하고, 편집은 DRAFT↔RETIRED만
  오간다(Literal이 1차 거부, 서비스가 재검증).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.requirements.service import (
    ChecklistItemView,
    PrerequisiteView,
    ProfileRequirementTemplateView,
    RequirementTemplateView,
)

AppliesTo = Literal["PRODUCT", "SKU", "FACILITY", "COMPANY", "INGREDIENT"]


class RequirementTemplateCreateRequest(BaseModel):
    market_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    applies_to: AppliesTo
    #: 대문자 코드 문자열(예: REGISTRATION, NOTIFICATION). 소문자는 서비스가 정규화.
    requirement_type: str = Field(min_length=1, max_length=40)
    validity_months: int | None = Field(default=None, ge=1)
    renewal_cycle_months: int | None = Field(default=None, ge=1)
    renewal_lead_days: int | None = Field(default=None, ge=1)
    #: 사람 표기 그대로(예: 1500000, 12.34) — 최소단위 환산은 서버가 한다
    #: (credit_limit 선례. 금액·통화는 한 쌍 — 서비스 검증+DB CHECK 이중).
    estimated_cost: Decimal | None = Field(default=None, ge=0, max_digits=15)
    estimated_cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    source_url: str | None = Field(default=None, max_length=500)
    last_verified_on: date | None = None
    note: str | None = None


class RequirementTemplateUpdateRequest(BaseModel):
    """내용 편집 — 확정 상태에서는 거부된다(409, ADR-0033).

    market_id는 받지 않는다 — 템플릿의 시장 축은 생성 시점에 고정이다(시장별
    요건 정의 — §5.1. 라벨의 SKU×시장 편집 지점 고정과 같은 계보).
    """

    #: 낙관 잠금(§17.2) — 목록·상세 응답의 version을 그대로 되돌려 준다.
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    applies_to: AppliesTo
    requirement_type: str = Field(min_length=1, max_length=40)
    validity_months: int | None = Field(default=None, ge=1)
    renewal_cycle_months: int | None = Field(default=None, ge=1)
    renewal_lead_days: int | None = Field(default=None, ge=1)
    estimated_cost: Decimal | None = Field(default=None, ge=0, max_digits=15)
    estimated_cost_currency: str | None = Field(default=None, min_length=3, max_length=3)
    source_url: str | None = Field(default=None, max_length=500)
    last_verified_on: date | None = None
    #: CONFIRMED는 여기 올 수 없다 — 확정은 확정 액션으로만(파일 독스트링).
    status: Literal["DRAFT", "RETIRED"]
    note: str | None = None


class TemplateActionRequest(BaseModel):
    """확정·초안 전환 액션 공통 본문 — 낙관 잠금 토큰만 담는다(§17.2)."""

    version: int = Field(ge=1)


class ChecklistItemAddRequest(BaseModel):
    seq: int = Field(ge=1)
    item_name: str = Field(min_length=1, max_length=200)
    #: 문서 종류 코드(§5.6 서류 참조 항목만) — 서류 없는 행정 절차 항목은 비움.
    document_type: str | None = Field(default=None, min_length=1, max_length=40)
    is_required: bool
    note: str | None = None


class ChecklistItemUpdateRequest(BaseModel):
    version: int = Field(ge=1)
    seq: int = Field(ge=1)
    item_name: str = Field(min_length=1, max_length=200)
    document_type: str | None = Field(default=None, min_length=1, max_length=40)
    is_required: bool
    note: str | None = None


class PrerequisiteAddRequest(BaseModel):
    prerequisite_template_id: int = Field(ge=1)


class ProfileRequirementTemplateAddRequest(BaseModel):
    requirement_template_id: int = Field(ge=1)


class RequirementTemplateSummary(BaseModel):
    id: int
    market_id: int
    market_code: str
    market_name: str
    name: str
    applies_to: str
    requirement_type: str
    validity_months: int | None
    renewal_cycle_months: int | None
    renewal_lead_days: int | None
    estimated_cost_amount: int | None
    estimated_cost_currency: str | None
    source_url: str | None
    last_verified_on: date | None
    status: str
    note: str | None
    version: int

    @classmethod
    def of(cls, view: RequirementTemplateView) -> RequirementTemplateSummary:
        return cls(**asdict(view))


class ChecklistItemSummary(BaseModel):
    id: int
    template_id: int
    seq: int
    item_name: str
    document_type_id: int | None
    document_type_code: str | None
    document_type_name: str | None
    is_required: bool
    note: str | None
    version: int

    @classmethod
    def of(cls, view: ChecklistItemView) -> ChecklistItemSummary:
        return cls(**asdict(view))


class PrerequisiteSummary(BaseModel):
    id: int
    template_id: int
    prerequisite_template_id: int
    prerequisite_name: str
    prerequisite_status: str
    version: int

    @classmethod
    def of(cls, view: PrerequisiteView) -> PrerequisiteSummary:
        return cls(**asdict(view))


class ProfileRequirementTemplateSummary(BaseModel):
    id: int
    item_profile_id: int
    requirement_template_id: int
    template_name: str
    template_status: str
    market_code: str
    version: int

    @classmethod
    def of(cls, view: ProfileRequirementTemplateView) -> ProfileRequirementTemplateSummary:
        return cls(**asdict(view))
