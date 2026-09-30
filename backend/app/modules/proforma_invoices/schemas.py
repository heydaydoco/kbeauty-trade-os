"""PI 요청·응답 (S3-1 design-integrated §2.1·§2.4 / design-A A4).

**생성 요청에는 원천 QT에 있는 값(SKU·단가·통화·환율·거래처·시장)을 다시 받는 필드가 존재하지 않는다** — 재입력 화면이
없다는 DoD ①을 요청 스키마의 구조로 보증한다(스키마 필드 집합 스냅샷 테스트). 재정의 화이트리스트는 결제조건·Incoterms·내부
메모·담당자뿐이다(환율·통화·단가·거래처·시장 변경은 QT에서 — 가격 변경은 새 QT). 낙관 잠금 필드는 저장소 관용대로 `version`
(원천 QT 헤더의 version)이다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.modules.trade_docs.schemas import (
    IncotermIn,
    IncotermOut,
    PaymentTermsIn,
    PaymentTermsOut,
    StatusLogOut,
)

__all__ = ["StatusLogOut"]  # 라우터가 이 모듈에서 가져온다(공용 조각의 재노출)


class PiLineRequest(BaseModel):
    """원천 QT 라인 1줄에서 가져올 수량 — 전체를 다 받으려면 `lines`를 생략한다(각 라인 잔량 전부)."""

    model_config = ConfigDict(extra="forbid")

    source_line_id: StrictInt = Field(ge=1)
    quantity: StrictInt


class PiOverrides(BaseModel):
    """PI 발행 시점에만 조정할 수 있는 화이트리스트 — 동결 후에는 어느 것도 바꿀 수 없다."""

    model_config = ConfigDict(extra="forbid")

    payment_terms: PaymentTermsIn | None = None
    incoterm: IncotermIn | None = None
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)


class ProformaInvoiceCreateRequest(BaseModel):
    """QT → PI 참조 생성(=발행·동결). `/preview`도 같은 본문을 받는다(비저장)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1, description="화면이 본 원천 QT 헤더 version(낙관 잠금)")
    doc_date: date | None = None
    valid_until: date
    bank_account_id: StrictInt = Field(ge=1)
    lines: list[PiLineRequest] | None = Field(default=None, min_length=1, max_length=500)
    overrides: PiOverrides | None = None
    copied_from_id: StrictInt | None = Field(default=None, ge=1)


class PiMetaUpdateRequest(BaseModel):
    """동결 후에도 고칠 수 있는 FREE 열(내부 메모·담당자)만 — 가격·조건 필드가 구조적으로 없다."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    internal_note: StrictStr | None = Field(default=None, max_length=1000)
    assignee_id: StrictInt | None = Field(default=None, ge=1)


# ── 응답 ────────────────────────────────────────────────────────────────────


class PiLineOut(BaseModel):
    id: int
    line_no: int
    qt_line_id: int
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


class BankSnapshotOut(BaseModel):
    """발행 시점 은행정보 스냅샷 — 계좌 마스터를 이후 수정·비활성해도 이 값은 불변이다."""

    account_id: int
    beneficiary_name: str
    beneficiary_address: str
    bank_name: str
    bank_address: str
    account_no: str
    swift_code: str


class AdvanceOut(BaseModel):
    """선수금 청구액 — 저장하지 않고 서버가 계산한다(HALF_UP 정수, 잔금=총액−선수금). 선수금 T/T가 아니면 없다."""

    advance_amount: int
    advance_text: str
    balance_amount: int
    balance_text: str


class ProformaInvoiceSummary(BaseModel):
    id: int
    doc_number: str
    doc_date: date
    status: str
    qt_id: int
    qt_doc_number: str | None
    buyer_partner_id: int
    buyer_name: str
    dest_market_code: str
    currency: str
    total_amount: int
    total_text: str
    valid_until: date
    #: 파생값(저장 아님) — 미입금 발행 상태인데 유효기간이 지난 PI의 배지.
    is_lapsed: bool
    assignee_id: int
    copied_from_id: int | None
    version: int
    created_at: datetime


class ProformaInvoiceDetail(ProformaInvoiceSummary):
    minor_units: int
    fx_rate: str | None
    fx_rate_date: date | None
    fx_rate_age_days: int | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    buyer_address: str
    internal_note: str | None
    frozen_at: datetime
    last_line_no: int
    bank: BankSnapshotOut
    advance: AdvanceOut | None
    lines: list[PiLineOut]

    @classmethod
    def of(cls, body: dict[str, Any]) -> ProformaInvoiceDetail:
        return cls.model_validate(body)


class PiPreviewLineOut(BaseModel):
    """미리보기 라인 — 아직 저장되지 않은 값이라 id가 없고 원천 라인 id와 (요청 시점) 잔량을 함께 준다."""

    line_no: int
    qt_line_id: int
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    quantity: int
    open_quantity_before: int
    buyer_item_code: str | None
    unit_price_amount: int
    unit_price_text: str
    line_amount: int
    line_amount_text: str
    price_basis: str
    is_free: bool
    price_reason: str | None


class ProformaInvoicePreview(BaseModel):
    """비저장 미리보기 — 채번·감사·이벤트·멱등 키 소비가 전혀 없다. 사용자가 확인한 뒤 같은 본문으로 생성한다."""

    qt_id: int
    qt_doc_number: str
    source_version: int
    doc_date: date
    valid_until: date
    currency: str
    minor_units: int
    buyer_partner_id: int
    buyer_name: str
    buyer_address: str
    dest_market_code: str
    fx_rate: str | None
    fx_rate_date: date | None
    payment_terms: PaymentTermsOut
    incoterm: IncotermOut
    internal_note: str | None
    assignee_id: int
    bank: BankSnapshotOut
    total_amount: int
    total_text: str
    advance: AdvanceOut | None
    lines: list[PiPreviewLineOut]
