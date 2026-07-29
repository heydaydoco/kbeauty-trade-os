"""A. 전표·정합 — "그때 단가" 조회 (DESIGN.md §4.1 / ADR-0017 / §20 A).

S3-1의 전표 단가 스냅샷이 이 함수를 소비한다. 여기가 틀리면 틀린 금액이
**확정**되고, 그건 §20 A의 "확정 건 단가 불변"으로도 못 잡는다 — 불변인 것과
처음부터 맞는 것은 다른 문제다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.money import Money, UnknownCurrencyError
from app.modules.catalog.models import SkuPrice
from app.modules.catalog.pricing import price_at
from tests.support.factories import create_sku

pytestmark = pytest.mark.group_a


def _record(
    sku_id: int, amount: str, on: date, *, price_type: str = "SALES", currency: str = "KRW"
) -> None:
    money = Money.from_decimal(Decimal(amount), currency)
    with unit_of_work() as uow:
        uow.session.add(
            SkuPrice(
                sku_id=sku_id,
                price_type=price_type,
                currency=money.currency,
                amount=money.amount,
                effective_from=on,
            )
        )


def test_the_price_in_effect_on_a_date_is_the_latest_one_before_it() -> None:
    """기준일에 적용되는 단가는 그 이전 발효분 중 가장 최근 것이다"""
    sku_id = create_sku()
    _record(sku_id, "10000", date(2026, 1, 1))
    _record(sku_id, "12000", date(2026, 4, 1))
    _record(sku_id, "15000", date(2026, 9, 1))

    assert price_at(
        sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 3, 31)
    ) == Money(10000, "KRW")
    assert price_at(
        sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 4, 1)
    ) == Money(12000, "KRW")
    assert price_at(
        sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 8, 31)
    ) == Money(12000, "KRW")


def test_the_effective_date_itself_counts() -> None:
    """발효일 당일부터 적용된다 (경계값)"""
    sku_id = create_sku()
    _record(sku_id, "12000", date(2026, 4, 1))

    assert (
        price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 4, 1)).amount
        == 12000
    )


def test_a_date_before_the_first_price_is_an_error_not_zero() -> None:
    """★ 최초 발효일 이전 기준일은 오류다 — 0도 null도 아니다 (ADR-0017)

    0을 돌려주면 금액 0원 전표가 조용히 확정되고, null을 돌려주면 호출자마다
    다른 기본값이 붙는다. 둘 다 나중에 원인을 찾을 수 없는 종류의 사고다.
    """
    sku_id = create_sku()
    _record(sku_id, "12000", date(2026, 4, 1))

    with pytest.raises(AppError) as exc:
        price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 3, 31))

    assert exc.value.code is ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE
    assert "2026-03-31" in str(exc.value.detail)


def test_no_price_at_all_is_the_same_error() -> None:
    """단가가 한 건도 없어도 같은 오류다 (조용한 기본값 없음)"""
    sku_id = create_sku()

    with pytest.raises(AppError) as exc:
        price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 4, 1))

    assert exc.value.code is ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE


def test_future_effective_prices_are_not_applied_yet() -> None:
    """미래 발효 단가는 아직 적용되지 않는다 (인상 예고를 미리 등록할 수 있다)"""
    sku_id = create_sku()
    _record(sku_id, "10000", date(2026, 1, 1))
    _record(sku_id, "15000", date(2027, 1, 1))

    assert (
        price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 6, 1)).amount
        == 10000
    )


def test_currencies_do_not_bleed_into_each_other() -> None:
    """통화가 다르면 다른 이력이다"""
    sku_id = create_sku()
    _record(sku_id, "12000", date(2026, 1, 1), currency="KRW")
    _record(sku_id, "9.90", date(2026, 1, 1), currency="USD")

    assert price_at(
        sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 6, 1)
    ) == Money(12000, "KRW")
    # USD 9.90 → 990센트. float을 거치지 않으므로 정확히 990이다(§2 ADR-02).
    assert price_at(
        sku_id=sku_id, price_type="SALES", currency="USD", on=date(2026, 6, 1)
    ) == Money(990, "USD")


def test_price_types_do_not_bleed_into_each_other() -> None:
    """판가와 매입가는 다른 이력이다"""
    sku_id = create_sku()
    _record(sku_id, "12000", date(2026, 1, 1), price_type="SALES")
    _record(sku_id, "7000", date(2026, 1, 1), price_type="PURCHASE")

    assert (
        price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 6, 1)).amount
        == 12000
    )
    assert (
        price_at(sku_id=sku_id, price_type="PURCHASE", currency="KRW", on=date(2026, 6, 1)).amount
        == 7000
    )


def test_an_unregistered_currency_fails_loudly() -> None:
    """등록되지 않은 통화는 조회 단계에서 실패한다 (money.py가 유일한 출처)"""
    sku_id = create_sku()

    with pytest.raises(UnknownCurrencyError):
        price_at(sku_id=sku_id, price_type="SALES", currency="XYZ", on=date(2026, 6, 1))


def test_a_past_price_stays_the_same_after_a_new_one_is_added() -> None:
    """새 단가를 추가해도 과거 기준일 조회 결과는 변하지 않는다 ("그때 단가")"""
    sku_id = create_sku()
    _record(sku_id, "10000", date(2026, 1, 1))
    before = price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 2, 1))

    _record(sku_id, "12000", date(2026, 4, 1))
    after = price_at(sku_id=sku_id, price_type="SALES", currency="KRW", on=date(2026, 2, 1))

    assert before == after == Money(10000, "KRW")
