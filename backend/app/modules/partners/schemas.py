"""거래처·품번 매핑·서명권자 요청·응답 (§4.6·§4.7).

여신한도는 요청에서 사람이 쓰는 표기(12.34)로 받고, 응답은 정수 최소단위
(`credit_limit_amount`)다 — sku_prices·BOM 단가와 같은 규약(ADR-0003).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.partners.models import PARTNER_TYPES
from app.modules.partners.service import ItemCodeView, PartnerView, SignatoryView

_PARTNER_TYPE_PATTERN = f"^({'|'.join(PARTNER_TYPES)})$"


# ── 거래처 마스터 ───────────────────────────────────────────────────────────


class PartnerCreateRequest(BaseModel):
    partner_code: str = Field(min_length=1, max_length=40)
    name_ko: str = Field(min_length=1, max_length=200)
    #: 유형 1개 이상(§4.6 "다중 유형") — 빈 목록은 서비스가 422로 거른다.
    type_codes: list[str] = Field(min_length=1, max_length=len(PARTNER_TYPES))
    #: 여신한도 — 사람이 쓰는 표기(12.34). 통화와 한 쌍이며 둘 다 비우면 "관리 안 함".
    #: 상한 15자리 — BOM 단가와 같은 이유(최소단위 환산 후 BIGINT 안).
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=15)
    credit_limit_currency: str | None = Field(default=None, min_length=3, max_length=3)
    #: DG 취급 가능 여부(포워더·3PL — §4.6). 비우면 "미확인"이다.
    dg_capable: bool | None = None
    strengths: str | None = None
    weaknesses: str | None = None
    note: str | None = None


class PartnerSummary(BaseModel):
    id: int
    partner_code: str
    name_ko: str
    type_codes: list[str]
    #: 정수 최소단위. 표시 변환은 통화별 자릿수(서버 제공)로만 한다.
    credit_limit_amount: int | None
    credit_limit_currency: str | None
    dg_capable: bool | None
    strengths: str | None
    weaknesses: str | None
    note: str | None

    @classmethod
    def of(cls, view: PartnerView) -> PartnerSummary:
        return cls(
            id=view.id,
            partner_code=view.partner_code,
            name_ko=view.name_ko,
            type_codes=list(view.type_codes),
            credit_limit_amount=view.credit_limit_amount,
            credit_limit_currency=view.credit_limit_currency,
            dg_capable=view.dg_capable,
            strengths=view.strengths,
            weaknesses=view.weaknesses,
            note=view.note,
        )


# ── 바이어 품번 매핑 ────────────────────────────────────────────────────────


class ItemCodeCreateRequest(BaseModel):
    sku_id: int
    buyer_item_code: str = Field(min_length=1, max_length=100)
    note: str | None = None


class ItemCodeSummary(BaseModel):
    id: int
    partner_id: int
    partner_code: str
    sku_id: int
    sku_code: str
    buyer_item_code: str
    note: str | None

    @classmethod
    def of(cls, view: ItemCodeView) -> ItemCodeSummary:
        return cls(
            id=view.id,
            partner_id=view.partner_id,
            partner_code=view.partner_code,
            sku_id=view.sku_id,
            sku_code=view.sku_code,
            buyer_item_code=view.buyer_item_code,
            note=view.note,
        )


# ── 서명권자 대장 ───────────────────────────────────────────────────────────


class SignatoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, max_length=100)
    note: str | None = None


class SignatorySummary(BaseModel):
    id: int
    name: str
    title: str | None
    note: str | None

    @classmethod
    def of(cls, view: SignatoryView) -> SignatorySummary:
        return cls(id=view.id, name=view.name, title=view.title, note=view.note)
