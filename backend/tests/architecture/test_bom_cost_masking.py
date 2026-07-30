"""K. 보안·품질 — BOM 원가 마스킹이 **스키마 분기**로 구현됐음을 지킨다 (ADR-0024).

§18.1은 마스킹을 API 응답 레벨에서 강제하라고 한다. 그 구현 방식에는 무너지는
방식과 버티는 방식이 있다:

  ✗ 직렬화한 dict에서 키를 지우기 — 새 응답 경로가 생길 때마다 사람이 기억해서
    지워야 하고, 한 번 잊으면 조용히 샌다.
  ✓ 응답 **스키마 자체를 나누기** — 원가 없는 스키마에는 담을 자리가 없다.

이 파일은 후자가 유지되는지, 그리고 갈림이 한 곳에서만 일어나는지를 본다.
원가 파생 합계·롤업이 나중에 생겨도 같은 규율이 적용되도록(웹 세션 판정 C-②)
"금액처럼 보이는 이름"을 통째로 막는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.logging.redaction import is_sensitive_key
from app.core.money import is_money_column_name
from app.modules.materials.schemas import BomLineCostHiddenSummary, BomLineSummary
from app.modules.materials.service import BomLineView, serialize_bom_line

pytestmark = pytest.mark.group_k

MODULE_ROOT = Path(__file__).resolve().parents[2] / "app" / "modules" / "materials"

#: 원가를 볼 수 있는 응답에만 있어야 하는 필드.
COST_FIELDS = frozenset({"unit_cost", "currency"})


def _fields(model: type) -> frozenset[str]:
    return frozenset(model.model_fields)


def test_the_masked_schema_has_no_cost_fields() -> None:
    """원가 없는 스키마에는 단가·통화 필드가 아예 없다 (null이 아니라 부재)"""
    assert not (_fields(BomLineCostHiddenSummary) & COST_FIELDS)


def test_the_two_schemas_differ_exactly_by_the_cost_fields() -> None:
    """두 스키마의 차이는 정확히 원가 두 필드다

    다른 필드까지 사라지면 조회 역할이 인증·원산지 업무를 못 한다(ADR-0024가
    행 단위 마스킹을 쓰지 않기로 한 이유). 반대로 차이가 더 좁아지면 원가가 샌다.
    """
    assert _fields(BomLineSummary) - _fields(BomLineCostHiddenSummary) == COST_FIELDS
    assert not (_fields(BomLineCostHiddenSummary) - _fields(BomLineSummary))


def test_the_masked_schema_carries_no_money_like_field() -> None:
    """★ 원가 없는 스키마에 금액·민감 이름의 필드가 하나도 없다

    지금은 단가뿐이지만, 파생 합계(라인 금액·BOM 총원가)가 생기면 같은 규율이
    적용돼야 한다(웹 세션 판정 C-②). 사람이 기억하는 대신 이름으로 막는다.
    """
    offenders = sorted(
        name
        for name in _fields(BomLineCostHiddenSummary)
        if is_money_column_name(name) or is_sensitive_key(name)
    )
    assert not offenders, (
        f"원가 없는 응답 스키마에 금액성 필드가 있습니다: {offenders} — "
        "파생 합계·롤업도 같은 부재 규율을 따릅니다(ADR-0024)."
    )


def test_serializer_omits_the_keys_rather_than_nulling_them() -> None:
    """직렬화가 키를 **빼고**, null로 남기지 않는다"""
    view = BomLineView(
        id=1,
        product_id=1,
        material_id=1,
        material_code="MAT-1",
        material_name_ko="정제수",
        material_type="RAW_MATERIAL",
        hs6=None,
        quantity=__import__("decimal").Decimal("1.5"),
        quantity_unit="g",
        unit_cost=1234,
        currency="USD",
        origin_status="UNKNOWN",
        note=None,
    )

    hidden = serialize_bom_line(view, include_cost=False)
    assert "unit_cost" not in hidden
    assert "currency" not in hidden
    assert "1234" not in str(hidden)
    # 업무에 필요한 값은 그대로 남는다.
    assert hidden["origin_status"] == "UNKNOWN"
    assert hidden["quantity"] == "1.5"

    shown = serialize_bom_line(view, include_cost=True)
    assert shown["unit_cost"] == 1234
    assert shown["currency"] == "USD"


def test_the_cost_decision_happens_in_exactly_one_place() -> None:
    """원가 노출 판정(`may_see_bom_cost`) 호출은 라우터 한 곳뿐이다

    갈림이 여러 곳으로 늘면 어느 하나가 빠졌는지 사람이 추적해야 한다.
    """
    callers = sorted(
        path.name
        for path in MODULE_ROOT.glob("*.py")
        if "may_see_bom_cost(" in path.read_text(encoding="utf-8")
        and path.name != "service.py"  # 정의부
    )
    assert callers == ["router.py"], (
        f"원가 노출 판정을 부르는 곳: {callers} — 갈림은 응답 경계 한 곳에서만 "
        "일어나야 합니다(ADR-0024)."
    )


def test_the_masking_rule_reuses_the_price_role_list() -> None:
    """BOM 원가와 매입가는 **같은 역할 목록**을 쓴다

    두 곳이 갈리면 한쪽에서 막은 원가가 다른 쪽으로 샌다.
    """
    source = (MODULE_ROOT / "service.py").read_text(encoding="utf-8")
    assert "from app.modules.catalog.pricing import may_see_cost" in source
    # 역할 목록을 여기서 **다시 정의**하면 두 곳이 갈린다(언급은 무방하다).
    assert "COST_VISIBLE_ROLES =" not in source, (
        "역할 목록을 여기서 다시 정의하지 마세요 — pricing.may_see_cost를 씁니다."
    )
    assert "RoleCode." not in source, (
        "역할을 직접 비교하지 마세요 — 판정은 pricing.may_see_cost 하나입니다."
    )
