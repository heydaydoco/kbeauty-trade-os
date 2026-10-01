"""입금 원장 API 스키마 (S3-1 PR-10a / design-E E7·E10). 쓰기 요청은 전부 `extra=forbid`다."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.core.pagination import Page
from app.modules.payments.models import REASON_MAX, REASON_MIN, REFERENCE_MAX

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_text(value: str, *, minimum: int, maximum: int) -> str:
    """제어문자(NUL·탭·개행·DEL 등 \\x00-\\x1f·\\x7f)를 거부하고 앞뒤 공백을 자른 뒤 **자른 값**으로 길이를 검증한다."""
    if _CONTROL_CHARS.search(value):
        raise ValueError("제어문자(줄바꿈·탭 등)는 입력할 수 없습니다.")
    cleaned = value.strip()
    if not minimum <= len(cleaned) <= maximum:
        raise ValueError(f"{minimum}~{maximum}자로 입력해 주세요.")
    return cleaned


class PaymentReceiptRequest(BaseModel):
    """PI 선수금 입금 기록 — 금액은 문자열("12.34"), 통화는 명시(PI 통화와 같아야 한다 — 환산 없음)."""

    model_config = ConfigDict(extra="forbid")

    received_amount: StrictStr = Field(min_length=1, max_length=30)
    received_currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    received_on: date
    #: 입금 확인 근거(은행 거래 참조·확인 메모) — 필수, 공백만은 불가. 유니크가 아니다.
    reference: StrictStr = Field(max_length=REFERENCE_MAX * 2)

    @field_validator("reference")
    @classmethod
    def _reference(cls, value: str) -> str:
        return _clean_text(value, minimum=1, maximum=REFERENCE_MAX)


class PaymentReversalRequest(BaseModel):
    """입금 역기록(전액 정정) — 사유 필수."""

    model_config = ConfigDict(extra="forbid")

    reason: StrictStr = Field(max_length=REASON_MAX * 2)

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return _clean_text(value, minimum=REASON_MIN, maximum=REASON_MAX)


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
