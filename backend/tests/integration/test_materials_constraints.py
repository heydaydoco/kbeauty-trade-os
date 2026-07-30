"""K. 보안·품질 — 자재·BOM·라벨의 불변식을 DB가 강제한다 (§4.4·§4.5·§17.5).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — 앱 검증만 보면 서비스를
  우회하는 경로(배치·직접 SQL)가 열려 있는지 알 수 없다. 모델이 선언한 CHECK가
  DB에 실재하는지를 여기서 실측한다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.modules.materials.models import Label, Material, ProductBom
from tests.support.factories import create_material, create_product, create_sku

pytestmark = pytest.mark.group_k


def _insert_material(**overrides: Any) -> None:
    columns: dict[str, Any] = {
        "material_code": "MAT-X",
        "name_ko": "테스트 자재",
        "material_type": "RAW_MATERIAL",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(Material(**columns))
        uow.session.flush()


def _insert_bom(**overrides: Any) -> None:
    columns: dict[str, Any] = {
        "quantity": Decimal("1"),
        "quantity_unit": "g",
        "origin_status": "UNKNOWN",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(ProductBom(**columns))
        uow.session.flush()


def _insert_label(**overrides: Any) -> None:
    columns: dict[str, Any] = {
        "country_code": "US",
        "label_version": 1,
        "language": "en",
        "approval_status": "DRAFT",
    }
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(Label(**columns))
        uow.session.flush()


# ── 자재 ───────────────────────────────────────────────────────────────────


def test_lot_managed_without_inventory_managed_is_rejected_by_the_database() -> None:
    """재고관리 없는 로트관리는 DB가 거부한다 — 원장에 없는 품목의 로트는 추적 대상이 없다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_material(lot_managed=True, inventory_managed=False)
    assert "lot_managed_requires_inventory_managed" in str(exc.value)


def test_lot_managed_with_inventory_managed_is_accepted() -> None:
    """둘 다 켜면 통과한다 — 사급 자재(§8.5)가 이 조합이다"""
    _insert_material(lot_managed=True, inventory_managed=True)


def test_malformed_hs6_is_rejected_by_the_database() -> None:
    """HS6은 숫자 6자리다 — 형식이 틀리면 DB가 거부한다(NULL은 통과)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_material(hs6="3304")
    assert "hs6_digits" in str(exc.value)


def test_unknown_material_type_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_material(material_type="MYSTERY")
    assert "material_type_valid" in str(exc.value)


# ── BOM ────────────────────────────────────────────────────────────────────


def test_cost_without_currency_is_rejected_by_the_database() -> None:
    """통화 없는 금액은 DB가 거부한다 (§2 ADR-02 "금액=최소단위+통화")"""
    with pytest.raises(IntegrityError) as exc:
        _insert_bom(
            product_id=create_product("PRD-C1"),
            material_id=create_material("MAT-C1"),
            unit_cost=1234,
            currency=None,
        )
    assert "cost_currency_pair" in str(exc.value)


def test_currency_without_cost_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_bom(
            product_id=create_product("PRD-C2"),
            material_id=create_material("MAT-C2"),
            unit_cost=None,
            currency="USD",
        )
    assert "cost_currency_pair" in str(exc.value)


def test_zero_quantity_is_rejected_by_the_database() -> None:
    """소요량 0은 거부된다 — 안 들어가는 자재는 라인을 만들지 않는다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_bom(
            product_id=create_product("PRD-Q"),
            material_id=create_material("MAT-Q"),
            quantity=Decimal("0"),
        )
    assert "quantity_positive" in str(exc.value)


def test_unknown_origin_status_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_bom(
            product_id=create_product("PRD-O"),
            material_id=create_material("MAT-O"),
            origin_status="MAYBE",
        )
    assert "origin_status_valid" in str(exc.value)


def test_origin_status_defaults_to_unknown_in_the_database() -> None:
    """★ 기본값이 DB 레벨에서도 미상이다 — 서비스를 우회해도 보수적이다"""
    product_id = create_product("PRD-D")
    material_id = create_material("MAT-D")
    with unit_of_work() as uow:
        row = ProductBom(
            product_id=product_id,
            material_id=material_id,
            quantity=Decimal("1"),
            quantity_unit="g",
        )
        uow.session.add(row)
        uow.session.flush()
        uow.session.refresh(row)
        assert row.origin_status == "UNKNOWN"


# ── 라벨 ───────────────────────────────────────────────────────────────────


def test_label_version_zero_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_label(sku_id=create_sku("SKU-L0"), label_version=0)
    assert "label_version_positive" in str(exc.value)


def test_lowercase_country_code_is_rejected_by_the_database() -> None:
    """소문자 국가코드는 DB가 거부한다 — 정규화를 우회한 경로도 막힌다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_label(sku_id=create_sku("SKU-LC"), country_code="us")
    assert "country_code_format" in str(exc.value)


def test_business_version_is_independent_of_the_locking_version() -> None:
    """★ 라벨 판번은 낙관적 잠금 카운터와 **다른 컬럼**이다 (§17.2)

    한 컬럼으로 겸하면 메모 한 줄 고칠 때마다 판번이 올라가고, 유일키
    (sku·시장·판번)까지 오염된다. 여기서 그 분리를 실측한다.
    """
    sku_id = create_sku("SKU-VER")
    with unit_of_work() as uow:
        row = Label(sku_id=sku_id, country_code="US", label_version=3, language="en")
        uow.session.add(row)
        uow.session.flush()
        label_id = row.id

    with unit_of_work() as uow:
        row = uow.session.get(Label, label_id)
        assert row is not None
        row.note = "메모만 고친다"

    with unit_of_work() as uow:
        row = uow.session.get(Label, label_id)
        assert row is not None
        assert row.label_version == 3  # 업무 판번은 그대로
        assert row.version == 2  # 잠금 카운터만 올라간다


# ── 리뷰 보강 (PR-2 적대적 리뷰 검출: CHECK 3건이 테스트에 없었다) ──────────


def test_negative_cost_is_rejected_by_the_database() -> None:
    """음수 단가는 DB가 거부한다 (0은 허용 — 무상 자재가 실재한다)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_bom(
            product_id=create_product("PRD-NEG"),
            material_id=create_material("MAT-NEG"),
            unit_cost=-1,
            currency="KRW",
        )
    assert "unit_cost_nonnegative" in str(exc.value)


def test_lowercase_currency_is_rejected_by_the_database() -> None:
    """소문자 통화는 DB가 거부한다 — 서비스 정규화를 우회한 경로도 막힌다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_bom(
            product_id=create_product("PRD-CUR"),
            material_id=create_material("MAT-CUR"),
            unit_cost=1000,
            currency="usd",
        )
    assert "currency_uppercase" in str(exc.value)


def test_unknown_approval_status_is_rejected_by_the_database() -> None:
    """정해지지 않은 라벨 승인상태는 DB가 거부한다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_label(sku_id=create_sku("SKU-AS"), approval_status="PENDING")
    assert "approval_status_valid" in str(exc.value)


def test_same_market_and_version_in_another_language_is_allowed() -> None:
    """★ 같은 시장·판번이라도 **언어가 다르면** 별개 라벨이다

    유일키에 language가 없으면 캐나다 영문판 v1을 넣은 뒤 불문판 v1을 못 넣어
    판번을 올려야 하고, 그 순간 판번이 "아트웍 개정"이 아니라 "언어 추가"로도
    증가해 §4.5 컷인의 기준이 흐려진다(리뷰 검출·수정 고정).
    """
    sku_id = create_sku("SKU-MULTILANG")
    _insert_label(sku_id=sku_id, country_code="CA", label_version=1, language="en")
    _insert_label(sku_id=sku_id, country_code="CA", label_version=1, language="fr")

    # 같은 시장·언어·판번의 두 번째 행만 거부된다.
    with pytest.raises(IntegrityError) as exc:
        _insert_label(sku_id=sku_id, country_code="CA", label_version=1, language="fr")
    assert "uq_labels_sku_id_country_code_language_label_version_active" in str(exc.value)
