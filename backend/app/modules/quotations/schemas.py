"""견적 요청·응답 (S3-1 design-integrated §2.1·§2.4 / design-A A4~A9).

요청 스키마는 전부 `extra="forbid"`이고 **`status`·`doc_number`·`total_amount`·`line_amount`·`version`(본문 헤더 제외)·
`frozen_at` 같은 서버 소유 필드가 구조적으로 없다** — 상태는 전이 엔드포인트로만, 합계는 서버가 라인에서 계산한다.
금액은 사람 표기 문자열(`"12.34"`)로 받고 서버가 정수 최소단위로 바꾼다(자릿수 초과는 거부, 반올림 없음). 응답은
정수 최소단위(`*_amount`)와 표시용 십진 문자열(`*_text`)을 함께 준다 — 프런트 산술 0.
낙관 잠금 필드는 저장소 관용대로 `version`이다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class PaymentTermsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_type: StrictStr
    #: 퍼센트 문자열("30", "33.33" — 소수 2자리까지). 프런트는 산술하지 않는다.
    advance_pct: StrictStr | None = None
    balance_anchor: StrictStr | None = None
    balance_days: StrictInt | None = None


class IncotermIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: StrictStr
    place: StrictStr
    year: StrictInt = 2020


class QuotationLineIn(BaseModel):
    """라인 입력 — 단가를 생략하면 마스터 판가(`price_at` 규칙), 주면 수동 입력(MANUAL)이다."""

    model_config = ConfigDict(extra="forbid")

    sku_id: StrictInt = Field(ge=1)
    quantity: StrictInt
    unit_price: StrictStr | None = None
    is_free: StrictBool = False
    price_reason: StrictStr | None = Field(default=None, max_length=200)
    buyer_item_code: StrictStr | None = Field(default=None, max_length=100)


class QuotationCreateRequest(BaseModel):
    """견적 작성(DRAFT). 복제는 `copied_from_id`(원본이 취소·만료·같은 바이어일 때만)."""

    model_config = ConfigDict(extra="forbid")

    buyer_partner_id: StrictInt = Field(ge=1)
    dest_market_code: StrictStr = Field(min_length=2, max_length=2)
    currency: StrictStr = Field(min_length=3, max_length=3)
    doc_date: date | None = None
    valid_until: date | None = None
    fx_rate: StrictStr | None = None
    fx_rate_date: date | None = None
    payment_terms: PaymentTermsIn | None = None
    incoterm: IncotermIn | None = None
    buyer_name: StrictStr | None = Field(default=None, max_length=200)
    buyer_address: StrictStr | None = Field(default=None, max_length=500)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)
    copied_from_id: StrictInt | None = Field(default=None, ge=1)
    lines: list[QuotationLineIn] = Field(default_factory=list, max_length=500)


class QuotationUpdateRequest(BaseModel):
    """초안(DRAFT) 편집 — 보낸 필드만 바뀐다. 동결 후 CONTENT 필드가 현재값과 다르면 409 FROZEN.

    `null`은 값 지우기다(결제조건·Incoterms·유효기간·환율·주소 — 발행 시 필수 완결성은 발행이 검사한다).
    """

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    buyer_partner_id: StrictInt | None = Field(default=None, ge=1)
    dest_market_code: StrictStr | None = Field(default=None, min_length=2, max_length=2)
    currency: StrictStr | None = Field(default=None, min_length=3, max_length=3)
    doc_date: date | None = None
    valid_until: date | None = None
    fx_rate: StrictStr | None = None
    fx_rate_date: date | None = None
    payment_terms: PaymentTermsIn | None = None
    incoterm: IncotermIn | None = None
    buyer_name: StrictStr | None = Field(default=None, max_length=200)
    buyer_address: StrictStr | None = Field(default=None, max_length=500)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)


class QuotationMetaUpdateRequest(BaseModel):
    """동결 후에도 고칠 수 있는 FREE 열(내부 메모·담당자)만 — 가격·조건 필드가 구조적으로 없다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)


class LineAddRequest(QuotationLineIn):
    version: StrictInt = Field(ge=1)


class LineUpdateRequest(BaseModel):
    """라인 부분 수정 — `sku_id`는 못 바꾼다(라인 삭제 후 신규 추가). 단가를 바꾸면 기준은 MANUAL이다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    quantity: StrictInt | None = None
    unit_price: StrictStr | None = None
    is_free: StrictBool | None = None
    price_reason: StrictStr | None = Field(default=None, max_length=200)
    buyer_item_code: StrictStr | None = Field(default=None, max_length=100)


# ── 응답 ────────────────────────────────────────────────────────────────────


class QuotationLineOut(BaseModel):
    id: int
    line_no: int
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    quantity: int
    buyer_item_code: str | None
    unit_price_amount: int
    unit_price_text: str
    list_price_amount: int | None
    list_price_text: str | None
    line_amount: int
    line_amount_text: str
    price_basis: str
    is_free: bool
    price_reason: str | None


class PaymentTermsOut(BaseModel):
    payment_type: str | None
    advance_pct: str | None
    advance_pct_bp: int | None
    balance_anchor: str | None
    balance_days: int | None


class IncotermOut(BaseModel):
    code: str | None
    place: str | None
    year: int | None


class QuotationSummary(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    buyer_partner_id: int
    buyer_name: str
    dest_market_code: str
    currency: str
    total_amount: int
    total_text: str
    valid_until: date | None
    #: 파생값(저장 아님) — 유효기간이 지났는데 스윕이 아직 안 돈 견적의 배지.
    is_lapsed: bool
    assignee_id: int
    copied_from_id: int | None
    version: int
    created_at: datetime


class QuotationDetail(QuotationSummary):
    minor_units: int
    fx_rate: str | None
    fx_rate_date: date | None
    #: 환율 기준일이 오늘(KST)로부터 며칠 지났는가(서버 계산 정수 — 프런트 산술 0).
    fx_rate_age_days: int | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    buyer_address: str | None
    internal_note: str | None
    frozen_at: datetime | None
    last_line_no: int
    lines: list[QuotationLineOut]

    @classmethod
    def of(cls, body: dict[str, Any]) -> QuotationDetail:
        return cls.model_validate(body)


class LineMutationOut(BaseModel):
    """라인 변경 응답 — 갱신된 헤더 version·합계를 실어 연속 편집이 자기 자신에게 409를 내지 않게 한다."""

    line: QuotationLineOut | None
    header_version: int
    total_amount: int
    total_text: str


class StatusLogOut(BaseModel):
    id: int
    occurred_at: datetime
    from_status: str | None
    to_status: str
    reason: str | None
    actor_user_id: int | None
    actor_name: str | None
    automatic: bool
