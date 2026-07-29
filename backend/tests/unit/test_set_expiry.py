"""A. 전표·정합 — 세트 로트 유통기한 = 구성품 MIN (DESIGN.md §4.2·§8.2 / GC-E1).

WBS S1-1 DoD: "세트 유통기한 MIN 계산 함수 존재(원장 연결은 P4)".
로트·원장이 서는 Phase 4가 구성품 로트를 고른 뒤 이 함수를 부른다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.catalog.sets import NoComponentExpiryError, set_lot_expiry

pytestmark = pytest.mark.group_a


def test_set_expiry_is_the_earliest_component_expiry() -> None:
    """세트 유통기한은 가장 빨리 만료되는 구성품을 따른다 (§4.2)"""
    result = set_lot_expiry(
        [date(2027, 5, 31), date(2026, 11, 30), date(2028, 1, 15)],
    )

    assert result == date(2026, 11, 30)


def test_order_does_not_matter() -> None:
    """구성품 순서가 결과를 바꾸지 않는다"""
    earliest = date(2026, 11, 30)
    later = date(2027, 5, 31)

    assert set_lot_expiry([earliest, later]) == set_lot_expiry([later, earliest]) == earliest


def test_single_component_returns_its_own_expiry() -> None:
    """구성품이 하나면 그 값이 곧 세트 유통기한이다"""
    assert set_lot_expiry([date(2027, 3, 1)]) == date(2027, 3, 1)


def test_identical_expiries_return_that_date() -> None:
    """전부 같은 날짜면 그 날짜다 (경계값)"""
    same = date(2027, 3, 1)
    assert set_lot_expiry([same, same, same]) == same


def test_empty_components_are_an_error_not_an_unlimited_shelf_life() -> None:
    """★ 구성품이 없으면 오류다 — "제한 없음"이 아니다

    빈 목록에 None이나 먼 미래를 돌려주면 유통기한 없는 세트 로트가 조용히
    생기고, 채널 최소기한 판정(§9)이 그것을 통과시킨다. S0-1에서 같은 형태의
    사고를 겪었다(PROGRESS: "빈 목록은 정상이 아니라 오류로 취급한다").
    """
    with pytest.raises(NoComponentExpiryError):
        set_lot_expiry([])


def test_a_generator_is_accepted() -> None:
    """제너레이터로 넘겨도 동작한다 (P4가 로트 조회 결과를 그대로 흘려보낸다)"""
    dates = (date(2027, 1, day) for day in (10, 5, 20))

    assert set_lot_expiry(dates) == date(2027, 1, 5)
