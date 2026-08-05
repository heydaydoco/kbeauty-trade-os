"""K. 보안·품질 — 거래처·유형·품번 매핑의 불변식을 DB가 강제한다 (§4.6·§17.4·§17.5).

★ 서비스를 거치지 않고 ORM으로 직접 INSERT한다 — 모델이 선언한 CHECK·부분
  유니크가 DB에 실재하는지를 실측한다(test_materials_constraints와 같은 방식).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.partners.models import CustomerItemCode, Partner, PartnerTypeLink
from tests.support.factories import create_partner, create_sku

pytestmark = pytest.mark.group_k


def _insert_partner(**overrides: Any) -> None:
    columns: dict[str, Any] = {"partner_code": "PTN-X", "name_ko": "테스트 거래처"}
    columns.update(overrides)
    with unit_of_work() as uow:
        uow.session.add(Partner(**columns))
        uow.session.flush()


def _insert_type_link(**overrides: Any) -> None:
    with unit_of_work() as uow:
        uow.session.add(PartnerTypeLink(**overrides))
        uow.session.flush()


def _insert_item_code(**overrides: Any) -> None:
    with unit_of_work() as uow:
        uow.session.add(CustomerItemCode(**overrides))
        uow.session.flush()


# ── 여신한도 — 금액 규약 (§2 ADR-02 / [M1] 보강(S1-3) ②) ────────────────────


def test_credit_without_currency_is_rejected_by_the_database() -> None:
    """통화 없는 여신한도는 DB가 거부한다 — 금액과 통화는 한 쌍이다"""
    with pytest.raises(IntegrityError) as exc:
        _insert_partner(credit_limit_amount=100_000, credit_limit_currency=None)
    assert "credit_limit_pair" in str(exc.value)


def test_currency_without_credit_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_partner(credit_limit_amount=None, credit_limit_currency="USD")
    assert "credit_limit_pair" in str(exc.value)


def test_negative_credit_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_partner(credit_limit_amount=-1, credit_limit_currency="USD")
    assert "credit_limit_amount_nonnegative" in str(exc.value)


def test_lowercase_credit_currency_is_rejected_by_the_database() -> None:
    with pytest.raises(IntegrityError) as exc:
        _insert_partner(credit_limit_amount=100, credit_limit_currency="usd")
    assert "credit_limit_currency_uppercase" in str(exc.value)


# ── 거래처 코드·유형 (§4.6 / ADR-0026) ─────────────────────────────────────


def test_duplicate_partner_code_is_rejected_by_the_database() -> None:
    create_partner("PTN-DUP")
    with pytest.raises(IntegrityError) as exc:
        _insert_partner(partner_code="PTN-DUP")
    assert "uq_partners_partner_code_active" in str(exc.value)


def test_soft_deleted_partner_code_can_be_reused() -> None:
    """soft delete 후 같은 코드 재유입은 신규다 (§17.4 부분 유니크)"""
    partner_id = create_partner("PTN-GONE")
    with unit_of_work() as uow:
        row = uow.session.get(Partner, partner_id)
        assert row is not None
        row.deleted_at = utcnow()
    _insert_partner(partner_code="PTN-GONE")


def test_unknown_partner_type_is_rejected_by_the_database() -> None:
    """유형 열거는 §4.6의 10종과 1:1이다 — 밖의 값은 DB가 거부한다"""
    partner_id = create_partner("PTN-T1")
    with pytest.raises(IntegrityError) as exc:
        _insert_type_link(partner_id=partner_id, type_code="MYSTERY")
    assert "type_code_valid" in str(exc.value)


def test_duplicate_partner_type_is_rejected_by_the_database() -> None:
    """같은 거래처에 같은 유형을 두 번 부여할 수 없다"""
    partner_id = create_partner("PTN-T2", types=("FORWARDER",))
    with pytest.raises(IntegrityError) as exc:
        _insert_type_link(partner_id=partner_id, type_code="FORWARDER")
    assert "uq_partner_type_links_partner_id_type_code_active" in str(exc.value)


# ── 바이어 품번 매핑 ([M1] 보강(S1-3) ③) ───────────────────────────────────


def test_duplicate_buyer_item_code_is_rejected_by_the_database() -> None:
    """(거래처, 바이어 품번)이 유일키다 — 같은 품번이 두 SKU를 가리킬 수 없다"""
    partner_id = create_partner("PTN-B1", types=("BUYER",))
    sku_a = create_sku("SKU-B1A")
    sku_b = create_sku("SKU-B1B")
    _insert_item_code(partner_id=partner_id, sku_id=sku_a, buyer_item_code="AMZ-001")
    with pytest.raises(IntegrityError) as exc:
        _insert_item_code(partner_id=partner_id, sku_id=sku_b, buyer_item_code="AMZ-001")
    assert "uq_customer_item_codes_partner_id_buyer_item_code_active" in str(exc.value)


def test_same_buyer_item_code_on_another_partner_is_allowed() -> None:
    """품번의 이름공간은 거래처다 — 다른 바이어의 같은 품번은 서로 무관하다"""
    partner_a = create_partner("PTN-B2A", types=("BUYER",))
    partner_b = create_partner("PTN-B2B", types=("BUYER",))
    sku_id = create_sku("SKU-B2")
    _insert_item_code(partner_id=partner_a, sku_id=sku_id, buyer_item_code="AMZ-002")
    _insert_item_code(partner_id=partner_b, sku_id=sku_id, buyer_item_code="AMZ-002")


def test_two_codes_for_the_same_sku_are_allowed() -> None:
    """같은 SKU에 복수 품번은 허용이다 — 바이어측 리뉴얼 실무([M1] 보강(S1-3) ③)"""
    partner_id = create_partner("PTN-B3", types=("BUYER",))
    sku_id = create_sku("SKU-B3")
    _insert_item_code(partner_id=partner_id, sku_id=sku_id, buyer_item_code="OLD-003")
    _insert_item_code(partner_id=partner_id, sku_id=sku_id, buyer_item_code="NEW-003")
