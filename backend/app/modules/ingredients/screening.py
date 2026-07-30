"""성분 스크리닝 — 판정이 아니라 **대조 리포트**다 (DESIGN.md §4.3 / §1 비범위).

■ 이것은 법적 판정이 아니다 (웹 세션 판정 D — 자동화 4금 비저촉)

  여기서 하는 일은 사용자가 근거를 확인해 등록한 규칙(ingredient_rules)과
  전성분의 **기계적 대조**뿐이다. 차단하지 않고(게이트 0), 저장하지 않고
  (§5.3 매트릭스와 같은 원칙 — 매 요청 계산), 결과에는 고정 문구
  "참고용·근거 확인"이 붙는다. 시장 판매 가부의 판단은 사람의 일이다.

  같은 이유로 출력 어디에도 "적합/통과/합격" 같은 판정 워딩을 쓰지 않는다 —
  한도 이내는 "한도 이내"라는 사실 서술만 한다. 결과 영속화의 부재는
  tests/architecture/test_screening_is_stateless.py가 기계로 지킨다.

■ 3분류 (§4.3 / WBS S1-2 DoD ①)

  금지(PROHIBITED)      — 대상국에 PROHIBITED 규칙이 있다.
  제한초과(OVER_LIMIT)  — RESTRICTED 규칙이 있고 함량이 한도를 넘거나,
                          함량이 미입력이라 한도와 비교할 수 없다(보수 분류 —
                          fail-closed: 모르는 것은 문제 있는 쪽으로 센다).
  미등재(UNLISTED)      — 그 성분×국가의 규칙 행이 **없다**. "안전"이 아니라
                          "우리 규칙 DB에 근거가 없으니 확인이 필요하다"는 뜻이다.

■ 전성분이 비어 있으면 오류다

  빈 리포트를 돌려주면 "검출 0건"과 구분되지 않는다 — 등록을 잊은 제품이
  "문제 없음"으로 읽히는 것이 최악의 실패 모양이다(빈 목록은 오류 취급 —
  PROGRESS 인계 ⑦).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.ingredients.models import Ingredient, IngredientRule, ProductIngredient
from app.modules.ingredients.service import require_product

#: §4.3의 고정 문구. 리포트가 판정이 아님을 응답 자체가 말한다.
NOTICE = "참고용·근거 확인"

CLASSIFICATION_PROHIBITED = "PROHIBITED"
CLASSIFICATION_OVER_LIMIT = "OVER_LIMIT"
CLASSIFICATION_UNLISTED = "UNLISTED"

#: 함량 미입력 제한 성분의 보수 분류 사유 — 사실 서술이다.
MISSING_CONCENTRATION_NOTE = "함량 미입력 — 한도와 비교할 수 없어 보수적으로 분류"


@dataclass(frozen=True, slots=True)
class ScreeningFinding:
    classification: str
    country_code: str
    ingredient_id: int
    inci_name: str
    ingredient_name_ko: str | None
    display_order: int
    concentration_pct: Decimal | None
    #: RESTRICTED 규칙의 한도. 금지·미등재는 None.
    max_concentration_pct: Decimal | None
    #: 규칙의 근거 (ADR-03). 미등재는 규칙이 없으므로 None이다.
    source_url: str | None
    last_verified_on: date | None
    note: str | None


@dataclass(frozen=True, slots=True)
class ScreeningReport:
    product_id: int
    countries: tuple[str, ...]
    notice: str
    #: 검사한 성분 수(전성분 행 수).
    checked_ingredient_count: int
    #: RESTRICTED 규칙이 있고 함량이 한도 이내였던 (성분×국가) 수 — 사실 서술.
    within_limit_count: int
    findings: tuple[ScreeningFinding, ...]


def _normalized_countries(raw: Sequence[str]) -> tuple[str, ...]:
    """대문자·중복 제거(순서 보존). 형식이 아니면 어느 값인지 짚어서 거절한다."""
    seen: list[str] = []
    for value in raw:
        code = str(value).strip().upper()
        if len(code) != 2 or not code.isalpha() or not code.isascii():
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"country": f"국가코드는 영문 2자입니다. 받은 값: {value!r}"},
            )
        if code not in seen:
            seen.append(code)
    if not seen:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"country": "대상국을 1개 이상 지정해 주세요. 예: ?country=US&country=EU"},
        )
    return tuple(seen)


def screen_product(*, product_id: int, countries: Sequence[str]) -> ScreeningReport:
    """전성분 × 대상국 규칙의 기계적 대조. 아무것도 쓰지 않는다 — SELECT뿐이다."""
    targets = _normalized_countries(countries)
    with unit_of_work() as uow:
        session = uow.session
        require_product(session, product_id)

        # ★ 성분 마스터의 deleted_at은 여기서 거르지 않는다 — 전성분 목록
        #   (service.list_product_ingredients)과 같은 기준이어야 한다. 마스터를
        #   보관 처리했다고 처방 라인이 검사에서 조용히 빠지면, 금지 성분이
        #   "문제 없음" 모양의 리포트로 새는 fail-open이다(리뷰 검출 — 라인
        #   자체의 제외는 전성분에서 빼는 것(soft delete)으로만 일어난다).
        formula = session.execute(
            select(ProductIngredient, Ingredient)
            .join(Ingredient, ProductIngredient.ingredient_id == Ingredient.id)
            .where(
                ProductIngredient.product_id == product_id,
                ProductIngredient.deleted_at.is_(None),
            )
            .order_by(ProductIngredient.display_order, ProductIngredient.id)
        ).all()
        if not formula:
            raise AppError(
                ErrorCode.INGREDIENTS_FORMULA_EMPTY, log_context={"product_id": product_id}
            )

        # 규칙은 한 번에 읽는다 — 성분×국가마다 조회하면 §18.4가 금지하는 N+1이다.
        rules = session.execute(
            select(IngredientRule).where(
                IngredientRule.ingredient_id.in_([row.Ingredient.id for row in formula]),
                IngredientRule.country_code.in_(targets),
                IngredientRule.deleted_at.is_(None),
            )
        ).scalars()
        rule_by_key: dict[tuple[int, str], IngredientRule] = {
            (rule.ingredient_id, rule.country_code): rule for rule in rules
        }

        findings: list[ScreeningFinding] = []
        within_limit = 0
        for line, ingredient in formula:
            for country in targets:
                rule = rule_by_key.get((ingredient.id, country))
                if rule is None:
                    findings.append(
                        _finding(CLASSIFICATION_UNLISTED, country, line, ingredient, None, None)
                    )
                elif rule.rule_type == "PROHIBITED":
                    findings.append(
                        _finding(CLASSIFICATION_PROHIBITED, country, line, ingredient, rule, None)
                    )
                elif line.concentration_pct is None:
                    # fail-closed: 모르는 함량은 문제 있는 쪽으로 센다(웹 세션 판정 D).
                    findings.append(
                        _finding(
                            CLASSIFICATION_OVER_LIMIT,
                            country,
                            line,
                            ingredient,
                            rule,
                            MISSING_CONCENTRATION_NOTE,
                        )
                    )
                elif line.concentration_pct > rule.max_concentration_pct:
                    findings.append(
                        _finding(CLASSIFICATION_OVER_LIMIT, country, line, ingredient, rule, None)
                    )
                else:
                    within_limit += 1

        return ScreeningReport(
            product_id=product_id,
            countries=targets,
            notice=NOTICE,
            checked_ingredient_count=len(formula),
            within_limit_count=within_limit,
            findings=tuple(findings),
        )


def _finding(
    classification: str,
    country: str,
    line: ProductIngredient,
    ingredient: Ingredient,
    rule: IngredientRule | None,
    note: str | None,
) -> ScreeningFinding:
    return ScreeningFinding(
        classification=classification,
        country_code=country,
        ingredient_id=ingredient.id,
        inci_name=ingredient.inci_name,
        ingredient_name_ko=ingredient.name_ko,
        display_order=line.display_order,
        concentration_pct=line.concentration_pct,
        max_concentration_pct=rule.max_concentration_pct if rule is not None else None,
        source_url=rule.source_url if rule is not None else None,
        last_verified_on=rule.last_verified_on if rule is not None else None,
        note=note,
    )


def serialize_report(report: ScreeningReport) -> dict[str, Any]:
    """멱등·화면 공용 직렬화 — Decimal은 문자열이다(float 금지, §2 ADR-02)."""

    def _text(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    return {
        "product_id": report.product_id,
        "countries": list(report.countries),
        "notice": report.notice,
        "checked_ingredient_count": report.checked_ingredient_count,
        "within_limit_count": report.within_limit_count,
        "findings": [
            {
                "classification": finding.classification,
                "country_code": finding.country_code,
                "ingredient_id": finding.ingredient_id,
                "inci_name": finding.inci_name,
                "ingredient_name_ko": finding.ingredient_name_ko,
                "display_order": finding.display_order,
                "concentration_pct": _text(finding.concentration_pct),
                "max_concentration_pct": _text(finding.max_concentration_pct),
                "source_url": finding.source_url,
                "last_verified_on": (
                    finding.last_verified_on.isoformat()
                    if finding.last_verified_on is not None
                    else None
                ),
                "note": finding.note,
            }
            for finding in report.findings
        ],
    }
