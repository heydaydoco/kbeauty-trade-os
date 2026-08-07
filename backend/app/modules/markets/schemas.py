"""시장 마스터 요청·응답 (§5.1 / S2-1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.markets.service import MarketView


class MarketCreateRequest(BaseModel):
    #: 대문자 2자(예: US, EU). 소문자는 서비스가 정규화한다('us'→'US' 선례).
    code: str = Field(min_length=2, max_length=2)
    name_ko: str = Field(min_length=1, max_length=100)
    name_en: str | None = Field(default=None, max_length=100)
    note: str | None = None


class MarketUpdateRequest(BaseModel):
    """정식화 편집 — 코드는 불변이라 받지 않는다(FK 참조 값)."""

    #: 낙관 잠금(§17.2) — 목록·상세 응답의 version을 그대로 되돌려 준다.
    version: int = Field(ge=1)
    name_ko: str = Field(min_length=1, max_length=100)
    name_en: str | None = Field(default=None, max_length=100)
    note: str | None = None


class MarketSummary(BaseModel):
    id: int
    code: str
    name_ko: str
    name_en: str | None
    note: str | None
    version: int

    @classmethod
    def of(cls, view: MarketView) -> MarketSummary:
        return cls(
            id=view.id,
            code=view.code,
            name_ko=view.name_ko,
            name_en=view.name_en,
            note=view.note,
            version=view.version,
        )
