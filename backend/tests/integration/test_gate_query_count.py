"""A. 게이트 평가의 질의 수는 라인 수와 무관하다 — N+1 회귀 검출 (S3-1 PR-11a / DESIGN §18.4 "행 수와 무관한 질의 수" / design-D D4 (f)).

품번 해석(`resolve_buyer_items` 1회)·SKU 조회(IN 1회)·준비도(`cells_for` 1회)·MOQ·가격(정책 1회, 기준가는 라인 스냅샷)은 모두 집합 질의다. 라인 2개일 때와 12개일 때 평가 전체의 SQL 문 수가 같다.
측정이 0을 세는 빈 검증이 되지 않도록 `> 0`을 함께 확인한다.
"""

from __future__ import annotations

import pytest

from tests.factories.gates import evaluate, passing_so
from tests.support.sqlcount import count_statements

pytestmark = pytest.mark.group_a


def test_the_number_of_statements_does_not_grow_with_the_number_of_lines() -> None:
    """라인 2개 SO와 12개 SO의 게이트 7종 평가가 같은 수의 SQL 문을 낸다(품번 매핑 라인 포함) — 라인별 질의(N+1)가 없다"""
    small = passing_so(skus=2, map_code=True)
    large = passing_so(skus=12, map_code=True)
    few = count_statements(lambda: evaluate(small["id"]))
    many = count_statements(lambda: evaluate(large["id"]))
    assert few > 0 and many > 0
    assert few == many, f"질의 수가 라인 수에 비례한다: 2줄={few}, 12줄={many}"
    # 참고값 평가(조회 경로)도 같은 규율 — 확정 통로 평가와 문 수가 라인 수에 무관하다
    assert count_statements(lambda: evaluate(small["id"], authoritative=True)) == count_statements(
        lambda: evaluate(large["id"], authoritative=True)
    )
