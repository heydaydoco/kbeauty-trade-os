"""K. 보안·품질 — 성분 규칙의 불변식을 DB가 강제한다 (§4.3·§17.5 / WBS S1-2).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — test_sku_constraints와 같은
  이유다. 앱 검증만 보면 서비스를 우회하는 경로(배치·직접 SQL)가 열려 있는지
  알 수 없다. 모델이 선언한 CHECK가 DB에 실재하는지를 본다(함정 ①의 방어 —
  신규 테이블이라 create_table에 포함됐어야 하고, 여기서 실측한다).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import today_kst
from app.modules.ingredients.models import Ingredient, IngredientRule, ProductIngredient
from tests.support.factories import create_ingredient, create_ingredient_rule, create_product

pytestmark = pytest.mark.group_k


def _insert_rule(**overrides: Any) -> None:
    columns: dict[str, Any] = {
        "country_code": "US",
        "rule_type": "PROHIBITED",
        "max_concentration_pct": None,
        "source_url": "https://example.com/regulation",
        "last_verified_on": today_kst(),
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(IngredientRule(**columns))
        uow.session.flush()


def test_restricted_without_a_limit_is_rejected_by_the_database() -> None:
    """한도 없는 제한은 DB가 거부한다 (양방향 CHECK — 판정 불능 행 차단)"""
    ingredient_id = create_ingredient("Phenoxyethanol")
    with pytest.raises(IntegrityError) as exc:
        _insert_rule(ingredient_id=ingredient_id, rule_type="RESTRICTED")
    assert "rule_type_limit_link" in str(exc.value)


def test_prohibited_with_a_limit_is_rejected_by_the_database() -> None:
    """한도 있는 금지는 DB가 거부한다 (양방향 CHECK — 모호 행 차단)"""
    ingredient_id = create_ingredient("Hydroquinone")
    with pytest.raises(IntegrityError) as exc:
        _insert_rule(
            ingredient_id=ingredient_id,
            rule_type="PROHIBITED",
            max_concentration_pct=Decimal("1.0"),
        )
    assert "rule_type_limit_link" in str(exc.value)


def test_second_rule_for_the_same_country_is_rejected_by_the_database() -> None:
    """같은 성분×국가 두 번째 규칙은 DB가 거부한다 — 공존 불허 (웹 세션 판정)"""
    ingredient_id = create_ingredient("Retinol")
    create_ingredient_rule(ingredient_id, country_code="EU", rule_type="PROHIBITED")

    with pytest.raises(IntegrityError) as exc:
        _insert_rule(
            ingredient_id=ingredient_id,
            country_code="EU",
            rule_type="RESTRICTED",
            max_concentration_pct=Decimal("0.3"),
        )
    assert "uq_ingredient_rules_ingredient_id_country_code_active" in str(exc.value)


def test_lowercase_country_code_is_rejected_by_the_database() -> None:
    """소문자 국가코드는 DB가 거부한다 — 정규화를 우회한 경로도 막힌다"""
    ingredient_id = create_ingredient("Aqua")
    with pytest.raises(IntegrityError) as exc:
        _insert_rule(ingredient_id=ingredient_id, country_code="us")
    assert "country_code_format" in str(exc.value)


def test_non_uppercase_normalized_name_is_rejected_by_the_database() -> None:
    """정규화 컬럼에 소문자가 들어오면 DB가 거부한다 — 유일키의 전제가 지켜진다"""
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(Ingredient(inci_name="Aqua", inci_name_normalized="Aqua"))
        uow.session.flush()
    assert "inci_name_normalized_upper" in str(exc.value)


def test_concentration_above_100_percent_is_rejected_by_the_database() -> None:
    """함량 100% 초과는 DB가 거부한다"""
    product_id = create_product()
    ingredient_id = create_ingredient("Glycerin")
    with pytest.raises(IntegrityError) as exc, unit_of_work() as uow:
        uow.session.add(
            ProductIngredient(
                product_id=product_id,
                ingredient_id=ingredient_id,
                concentration_pct=Decimal("120"),
                display_order=1,
            )
        )
        uow.session.flush()
    assert "concentration_pct_max" in str(exc.value)
