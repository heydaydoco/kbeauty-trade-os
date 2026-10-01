"""입금 원장 API 스키마 (S3-1 PR-10a / design-E E7·E10). 쓰기 요청은 전부 `extra=forbid`다."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from app.core.pagination import Page
from app.modules.payments.models import REASON_MAX, REASON_MIN, REFERENCE_MAX


class PaymentReceiptRequest(BaseModel):
    """PI 선수금 입금 기록 — 금액은 문자열("12.34"), 통화는 명시(PI 통화와 같아야 한다 — 환산 없음)."""

    model_config = ConfigDict(extra="forbid")

    received_amount: StrictStr = Field(min_length=1, max_length=30)
    received_currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    received_on: date
    #: 입금 확인 근거(은행 거래 참조·확인 메모) — 필수, 공백만은 불가. 유니크가 아니다.
    reference: StrictStr = Field(min_length=1, max_length=REFERENCE_MAX, pattern=r"\S")


class PaymentReversalRequest(BaseModel):
    """입금 역기록(전액 정정) — 사유 필수."""

    model_config = ConfigDict(extra="forbid")

    reason: StrictStr = Field(min_length=REASON_MIN, max_length=REASON_MAX, pattern=r"\S.*\S")


class PaymentOut(BaseModel):
    id: int
    pi_id: int
    partner_id: int
    kind: Literal["RECEIPT", "REVERSAL"]
    received_amount: int
    received_amount_text: str
    received_currency: str
    received_on: date
    reference: str
    reverses_payment_id: int | None
    #: 입금 행에만 — 그것을 되돌린 역기록 행의 id(없으면 null).
    reversed_by_payment_id: int | None
    reason: str | None
    recorded_by_id: int
    created_at: datetime


class PaymentSummaryOut(BaseModel):
    pi_id: int
    pi_status: str
    currency: str
    payment_type: str | None
    due_amount: int
    due_text: str | None
    net_received_amount: int
    net_received_text: str | None
    remaining_amount: int
    remaining_text: str | None


class PaymentWarningOut(BaseModel):
    code: Literal["CONFIRMED_SO_ADVANCE_UNMET"]
    sales_order_ids: list[int]


class PaymentWriteOut(BaseModel):
    payment: PaymentOut
    summary: PaymentSummaryOut
    #: 확정 SO가 이 PI를 근거로 있고 역기록 뒤 순입금이 선수금 청구액 미만이면 경고 1건(역기록은 막지 않고 SO를 취소하지 않는다).
    warnings: list[PaymentWarningOut]


class PaymentPage(Page[PaymentOut]):
    summary: PaymentSummaryOut
