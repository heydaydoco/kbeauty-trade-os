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


def _branchy_so(lines: int) -> dict[str, object]:
    """BLOCK·WARN 분기를 전부 타는 SO — 무상 라인(가격 WARN)·단종 SKU·MOQ 미달·준비도 RED·가격 편차 BLOCK(+override 부여)."""
    from sqlalchemy import text

    from app.core.db.session import owner_engine
    from app.modules.gates.types import GateCode
    from app.modules.identity.models import RoleCode
    from app.modules.trade_chain import gate_flow
    from tests.factories.gates import line_ids, make_free, one, set_line, set_policy
    from tests.factories.payments import actor

    so = passing_so(skus=lines, market_color="RED", moq=1000, quantity=5)
    set_policy("price_deviation_tolerance_bp", 500)
    make_free(so["id"], 1)
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE skus SET status = 'DISCONTINUED' WHERE id = :s"), {"s": so["sku_ids"][1]}
        )
    set_line(so["id"], 3, unit_price=1100, list_price=1000)
    line = line_ids(so["id"])[2]
    price = one(evaluate(so["id"]), GateCode.PRICE_DEVIATION, line)
    gate_flow.grant_gate_override(
        actor=actor(RoleCode.ADMIN),
        idempotency_key=f"qc-{lines}",
        so_id=so["id"],
        payload={
            "gate_code": "PRICE_DEVIATION",
            "line_id": line,
            "basis_hash": price.basis_hash,
            "reason": "질의 수 시험 사유",
        },
    )
    return so


def test_branchy_evaluation_and_the_full_gate_report_are_constant_in_the_line_count() -> None:
    """무상·단종·MOQ 미달·RED·BLOCK+override 분기를 전부 타는 SO에서도 평가와 `GET /gates` 전체 경로(override 정보·정산 포함)의 SQL 문 수가 라인 수(4→12)와 무관하다"""
    from app.modules.identity.models import RoleCode
    from tests.factories.gates import gates_of
    from tests.factories.trade import logged_in

    small, large = _branchy_so(4), _branchy_so(12)
    assert count_statements(lambda: evaluate(small["id"])) == count_statements(
        lambda: evaluate(large["id"])
    )
    with logged_in(RoleCode.TRADE) as client:
        gates_of(client, small["id"])  # 워밍업(요청 고정 비용)
        few = count_statements(lambda: gates_of(client, small["id"]))
        many = count_statements(lambda: gates_of(client, large["id"]))
        report = gates_of(client, large["id"])
    assert few > 0 and many > 0 and few == many, (
        f"GET 질의 수가 라인에 비례: 4줄={few}, 12줄={many}"
    )
    levels = {g["level"] for g in report["gates"]}
    assert {"BLOCK", "WARN"} <= levels  # 분기가 실제로 탔다(빈 시나리오 방지)
    assert any(g["settlement"] == "OVERRIDDEN" for g in report["gates"])
