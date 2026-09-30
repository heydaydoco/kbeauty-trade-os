"""전표 요청·응답 공용 스키마 조각 — 결제조건·Incoterms·상태이력 (S3-1 design-A A5·A6 / design-B B6).

QT·PI(·SO·PO)가 같은 모양을 쓴다. 요청 조각은 `extra="forbid"`다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr


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


class StatusLogOut(BaseModel):
    id: int
    occurred_at: datetime
    from_status: str | None
    to_status: str
    reason: str | None
    actor_user_id: int | None
    actor_name: str | None
    automatic: bool
