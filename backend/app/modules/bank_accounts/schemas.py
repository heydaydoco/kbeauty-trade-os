"""은행 계좌 요청·응답 (S3-1 design-A A8). 요청 스키마는 전부 `extra="forbid"`."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class BankAccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: StrictStr = Field(min_length=1, max_length=80, description="선택 목록에 보이는 이름")
    currency: StrictStr = Field(min_length=3, max_length=3)
    beneficiary_name: StrictStr = Field(min_length=1, max_length=200)
    beneficiary_address: StrictStr = Field(min_length=1, max_length=300)
    bank_name: StrictStr = Field(min_length=1, max_length=200)
    bank_address: StrictStr = Field(min_length=1, max_length=300)
    account_no: StrictStr = Field(min_length=1, max_length=40)
    swift_code: StrictStr = Field(min_length=8, max_length=11)


class BankAccountUpdateRequest(BaseModel):
    """보낸 필드만 바뀐다. **통화는 바꿀 수 없다**(가격·PI가 통화별이다 — 새 계좌를 등록한다)."""

    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)
    label: StrictStr | None = Field(default=None, min_length=1, max_length=80)
    beneficiary_name: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    beneficiary_address: StrictStr | None = Field(default=None, min_length=1, max_length=300)
    bank_name: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    bank_address: StrictStr | None = Field(default=None, min_length=1, max_length=300)
    account_no: StrictStr | None = Field(default=None, min_length=1, max_length=40)
    swift_code: StrictStr | None = Field(default=None, min_length=8, max_length=11)


class BankAccountOut(BaseModel):
    id: int
    label: str
    currency: str
    beneficiary_name: str
    beneficiary_address: str
    bank_name: str
    bank_address: str
    account_no: str
    swift_code: str
    version: int
    created_at: datetime
