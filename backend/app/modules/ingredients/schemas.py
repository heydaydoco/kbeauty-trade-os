"""성분 요청·응답 (§4.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.modules.ingredients.models import INGREDIENT_RULE_TYPES
from app.modules.ingredients.screening import (
    CLASSIFICATION_OVER_LIMIT,
    CLASSIFICATION_PROHIBITED,
    CLASSIFICATION_UNLISTED,
)
from app.modules.ingredients.service import (
    IngredientRuleView,
    IngredientView,
    ProductIngredientView,
)

_RULE_TYPE_PATTERN = f"^({'|'.join(INGREDIENT_RULE_TYPES)})$"


# ── 성분 마스터 ─────────────────────────────────────────────────────────────


class IngredientCreateRequest(BaseModel):
    #: INCI 표기 원문. 공백 축약 외에는 서비스가 손대지 않는다(라벨 인쇄용).
    inci_name: str = Field(min_length=1, max_length=200)
    name_ko: str | None = Field(default=None, max_length=200)
    note: str | None = None


class IngredientSummary(BaseModel):
    id: int
    inci_name: str
    name_ko: str | None
    note: str | None

    @classmethod
    def of(cls, view: IngredientView) -> IngredientSummary:
        return cls(id=view.id, inci_name=view.inci_name, name_ko=view.name_ko, note=view.note)


# ── 성분×국가 규칙 (§4.3 / ADR-03) ─────────────────────────────────────────


class IngredientRuleCreateRequest(BaseModel):
    """규칙 **기록** 요청 — 시스템은 판정하지 않는다(§1 비범위)."""

    #: ISO 3166-1 alpha-2. 소문자로 와도 서비스가 대문자로 정규화한다.
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    rule_type: str = Field(pattern=_RULE_TYPE_PATTERN)
    #: 농도 한도(%). RESTRICTED 전용 — 짝 검증은 서비스·DB CHECK가 한다.
    #: Decimal로 받는다. float으로 받으면 0.1이 0.1000000000000000055…가 된다(§2 ADR-02).
    max_concentration_pct: Decimal | None = Field(
        default=None, gt=0, le=100, max_digits=7, decimal_places=4
    )
    #: ADR-03: 근거링크 필수. 링크가 아니면 근거가 아니다.
    source_url: str = Field(max_length=500, pattern=r"^https?://")
    #: 최종확인일(업무 날짜). 미래 날짜는 서비스가 거절한다.
    last_verified_on: date


class IngredientRuleSummary(BaseModel):
    id: int
    ingredient_id: int
    country_code: str
    rule_type: str
    max_concentration_pct: Decimal | None
    source_url: str
    last_verified_on: date

    @classmethod
    def of(cls, view: IngredientRuleView) -> IngredientRuleSummary:
        return cls(
            id=view.id,
            ingredient_id=view.ingredient_id,
            country_code=view.country_code,
            rule_type=view.rule_type,
            max_concentration_pct=view.max_concentration_pct,
            source_url=view.source_url,
            last_verified_on=view.last_verified_on,
        )


# ── 전성분 (제품×성분) ──────────────────────────────────────────────────────


class ProductIngredientCreateRequest(BaseModel):
    ingredient_id: int
    #: 함량(%). 비공개 처방은 비워 둘 수 있다 — 스크리닝이 fail-closed로 다룬다.
    concentration_pct: Decimal | None = Field(
        default=None, gt=0, le=100, max_digits=7, decimal_places=4
    )
    #: 전성분 표시순서(1부터). 상한이 없으면 int4 범위 밖 값이 DB에서 500으로
    #: 터진다 — 전성분 수백 행이 실무 상한이라 9999면 충분하다.
    display_order: int = Field(gt=0, le=9999)


class ProductIngredientSummary(BaseModel):
    id: int
    product_id: int
    ingredient_id: int
    inci_name: str
    ingredient_name_ko: str | None
    concentration_pct: Decimal | None
    display_order: int

    @classmethod
    def of(cls, view: ProductIngredientView) -> ProductIngredientSummary:
        return cls(
            id=view.id,
            product_id=view.product_id,
            ingredient_id=view.ingredient_id,
            inci_name=view.inci_name,
            ingredient_name_ko=view.ingredient_name_ko,
            concentration_pct=view.concentration_pct,
            display_order=view.display_order,
        )


# ── 스크리닝 리포트 (§4.3 — 판정 아님·미저장) ──────────────────────────────

_CLASSIFICATION_PATTERN = (
    f"^({CLASSIFICATION_PROHIBITED}|{CLASSIFICATION_OVER_LIMIT}|{CLASSIFICATION_UNLISTED})$"
)


class ScreeningFindingSummary(BaseModel):
    """3분류 검출 1건 — 금지 / 제한초과(함량 미입력 보수 분류 포함) / 미등재."""

    classification: str = Field(pattern=_CLASSIFICATION_PATTERN)
    country_code: str
    ingredient_id: int
    inci_name: str
    ingredient_name_ko: str | None
    display_order: int
    concentration_pct: Decimal | None
    max_concentration_pct: Decimal | None
    source_url: str | None
    last_verified_on: date | None
    note: str | None


class ScreeningReportSummary(BaseModel):
    product_id: int
    countries: list[str]
    #: 고정 문구 "참고용·근거 확인" (§4.3) — 리포트가 판정이 아님을 응답이 말한다.
    notice: str
    checked_ingredient_count: int
    #: 한도 이내였던 (성분×국가) 수 — 사실 서술이다. 판정 워딩을 쓰지 않는다.
    within_limit_count: int
    findings: list[ScreeningFindingSummary]
