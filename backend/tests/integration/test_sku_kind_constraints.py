"""K. 보안·품질 — SKU 종류↔처방 짝을 DB가 강제한다 (DESIGN.md §4.1·§4.2·§17.5 / ADR-0016).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다.
  앱 코드의 검증만 검사하면, 마이그레이션·배치·직접 SQL처럼 서비스를 우회하는
  경로가 열려 있는지 알 수 없다. §17.5가 "가능한 불변식은 CHECK"라고 하는 이유가
  그것이고, 이 파일은 그 CHECK가 실제로 걸려 있는지를 본다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.modules.catalog.models import Sku
from tests.support.factories import create_product

pytestmark = pytest.mark.group_k


def _insert_sku(**columns: Any) -> None:
    with unit_of_work() as uow:
        uow.session.add(Sku(**columns))
        uow.session.flush()


def test_single_sku_without_a_formulation_is_rejected_by_the_database() -> None:
    """처방 없는 단품은 DB가 거부한다 (판정 단위가 사라진 SKU가 생기면 안 된다)"""
    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SER-001", name_ko="처방 없는 단품", kind="SINGLE", product_id=None)

    assert "kind_product_link" in str(exc.value)


def test_set_sku_with_a_formulation_is_rejected_by_the_database() -> None:
    """처방을 단 세트는 DB가 거부한다 (§4.2 — 판정은 구성품별 롤업이다)"""
    product_id = create_product()

    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SET-001", name_ko="처방 단 세트", kind="SET", product_id=product_id)

    assert "kind_product_link" in str(exc.value)


def test_unknown_kind_is_rejected_by_the_database() -> None:
    """정해지지 않은 종류 값은 DB가 거부한다 (§17.5 CHECK)

    ★ 어느 CHECK 이름으로 거부되는지는 못박지 않는다. 'BUNDLE'은 kind_valid도
      깨지만, "SINGLE 아니면 SET"을 전제로 하는 kind_product_link도 함께 깬다 —
      PostgreSQL은 둘 중 먼저 걸린 하나만 보고하고 그 순서는 우리가 정하는 게
      아니다. 이름을 못박으면 기능과 무관한 이유로 빨개진다.
    """
    product_id = create_product()

    with pytest.raises(IntegrityError) as exc:
        _insert_sku(sku_code="SER-001", name_ko="이상한 종류", kind="BUNDLE", product_id=product_id)

    message = str(exc.value)
    assert "ck_skus_kind_valid" in message or "ck_skus_kind_product_link" in message


def test_both_kind_check_constraints_exist() -> None:
    """두 CHECK가 실제로 DB에 걸려 있다 (거동 테스트가 한쪽만 증명하므로 이름으로 확인)"""
    with unit_of_work() as uow:
        names = set(
            uow.session.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'public.skus'::regclass AND contype = 'c'
                    """
                )
            )
            .scalars()
            .all()
        )

    assert {"ck_skus_kind_valid", "ck_skus_kind_product_link"} <= names


def test_valid_pairings_are_accepted() -> None:
    """올바른 짝은 통과한다 (위 거부들이 "무엇이든 거부"가 아님을 증명한다)

    ★ 이 자기검사가 없으면, 제약이 과하게 좁아져 정상 등록까지 막는 상태에서도
      위 세 테스트는 그대로 초록이다.
    """
    _insert_sku(
        sku_code="SER-001", name_ko="단품", kind="SINGLE", product_id=create_product("PRD-001")
    )
    _insert_sku(sku_code="SET-001", name_ko="세트", kind="SET", product_id=None)
