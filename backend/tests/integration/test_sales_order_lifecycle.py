"""A. SO 상태 전이 — SO 8상태 56쌍 전수·통로 규칙·예약 상태·재개 목표·확정 액션 엣지 (S3-1 PR-7a / ADR-0051·0052 / design-B B1·B2·B3).

SO 8상태의 순서쌍 56개: 허용 10(사람 8·자동 2 — S3-2 PR-3a CONFIRMED↔IN_SHIPMENT 선적 수렴, ADR-0075)은 성공, 미허용 46은 409 전건
(ADR-0038의 전수 선례). RESERVED 3상태(PARTIALLY_ALLOCATED·ALLOCATED·COMPLETED)는 in/out 엣지가 0이다(소비 세션 S4-2·S3-3이 더한다). 확정(RECEIVED→CONFIRMED)은 동결 액션 전용 엣지라
PR-12의 `confirm`이 오기 전까지 이 파일이 `via_freeze_action=True` 직접 호출로 시험한다(죽은 문이 아니라 상태 기계의 실제 진입점).
"""

from __future__ import annotations

from itertools import permutations
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.modules.identity.models import RoleCode
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import (
    AUTO_TRANSITIONS,
    FREEZE_ACTION_EDGES,
    HUMAN_TRANSITIONS,
    RESERVED,
    STATUSES,
)
from app.modules.trade_docs.transition import record_transition
from tests.factories.trade import raw_so
from tests.support.factories import create_user

pytestmark = pytest.mark.group_a

KIND = DocKind.SALES_ORDER
STATES = STATUSES[KIND]
HUMAN = HUMAN_TRANSITIONS[KIND]
AUTO = AUTO_TRANSITIONS[KIND]
FREEZE = FREEZE_ACTION_EDGES[KIND]


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _log_rows(so_id: int) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [
            tuple(r)
            for r in connection.execute(
                text(
                    "SELECT from_status, to_status, automatic, reason, actor_user_id FROM"
                    " sales_order_status_log WHERE sales_order_id = :s ORDER BY id"
                ),
                {"s": so_id},
            )
        ]


def _events(so_id: int) -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM events WHERE aggregate_type = 'sales_orders'"
            " AND aggregate_id = :i AND event_type = 'sales_orders.sales_order.status_changed'",
            i=so_id,
        )
    )


def _transition(so_id: int, to: str, **kwargs: Any) -> str:
    with unit_of_work() as uow:
        row = uow.session.get(SalesOrder, so_id)
        assert row is not None
        if kwargs.get("via_freeze_action") and row.status == "RECEIVED":
            # M10 — 동결 시각은 확정 증적(여신·PI 판정)과 같은 flush에 쓰여야 CHECK를 지킨다(실제 확정 통로는 `confirm_sales_order`가 채운다).
            row.credit_verdict = "NOT_MANAGED"
            row.pi_gate_verdict = "NOT_APPLICABLE"
        return record_transition(uow.session, row, to, **kwargs)


_ALL_PAIRS = list(permutations(STATES, 2))


def _kwargs_for(pair: tuple[str, str], actor: int) -> dict[str, Any]:
    _frm, to = pair
    return {
        "actor_user_id": actor,
        "reason": "테스트 사유" if to in ("CANCELLED", "ON_HOLD") else None,
        "automatic": pair in AUTO,  # 선적 수렴 2엣지는 자동 통로로만(행위자 = 유발자)
        "via_freeze_action": pair in FREEZE,
    }


def _make(pair: tuple[str, str]) -> int:
    """보류에서 CONFIRMED로 재개하는 쌍은 '확정 뒤 보류'여야 결속 CHECK를 지킨다 — 그 밖엔 상태가 요구하는 기본 확정 시각."""
    frm, to = pair
    return raw_so(frm, confirmed=True if (frm, to) == ("ON_HOLD", "CONFIRMED") else None)


@pytest.mark.parametrize("pair", _ALL_PAIRS, ids=[f"{a}->{b}" for a, b in _ALL_PAIRS])
def test_all_fifty_six_so_pairs(pair: tuple[str, str]) -> None:
    """SO 56쌍 — 허용 10쌍은 성공(이력 1행·automatic은 자동 엣지에서만 true·이벤트 1건), 미허용 46쌍은 409 TRANSITION.NOT_ALLOWED이고 무변"""
    frm, to = pair
    actor = create_user(f"so-actor-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    so_id = _make(pair)
    before_log, before_events = len(_log_rows(so_id)), _events(so_id)
    if pair in HUMAN | AUTO:
        assert _transition(so_id, to, **_kwargs_for(pair, actor)) == frm
        assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id) == to
        rows = _log_rows(so_id)
        assert len(rows) == before_log + 1 and _events(so_id) == before_events + 1
        assert rows[-1][:3] == (frm, to, pair in AUTO)
        if to in ("CANCELLED", "ON_HOLD"):
            assert rows[-1][3] == "테스트 사유"
        # 확정 시각은 동결 액션(RECEIVED→CONFIRMED)만 채운다 — 그 밖의 전이는 건드리지 않는다
        confirmed = _scalar(
            "SELECT confirmed_at IS NOT NULL FROM sales_orders WHERE id = :i", i=so_id
        )
        assert confirmed == (
            pair in FREEZE
            or frm in ("CONFIRMED", "IN_SHIPMENT")
            or (frm, to) == ("ON_HOLD", "CONFIRMED")
        )
    else:
        with pytest.raises(AppError) as caught:
            _transition(so_id, to, **_kwargs_for(pair, actor))
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
        assert caught.value.status_code == 409
        assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id) == frm
        assert len(_log_rows(so_id)) == before_log and _events(so_id) == before_events


def test_the_pair_table_itself_is_not_vacuous() -> None:
    """공회전 방지 — 56쌍 중 허용 10(사람 8·자동 2)·미허용 46이 실제로 갈리고, RESERVED 3상태 관련 쌍은 전부 미허용이다"""
    assert len(_ALL_PAIRS) == 56
    assert len([p for p in _ALL_PAIRS if p in HUMAN | AUTO]) == 10
    assert (len(HUMAN), len(AUTO)) == (8, 2)
    reserved = RESERVED[KIND]
    assert reserved == {"PARTIALLY_ALLOCATED", "ALLOCATED", "COMPLETED"}
    assert not [
        p for p in _ALL_PAIRS if (p[0] in reserved or p[1] in reserved) and p in HUMAN | AUTO
    ]


@pytest.mark.parametrize("pair", sorted(HUMAN))
def test_wrong_channel_is_rejected_even_for_an_allowed_pair(pair: tuple[str, str]) -> None:
    """허용 쌍도 통로가 틀리면 거부 — 사람 쌍에 automatic=True(사람 쌍은 자동 집합에 없다) · 동결 엣지는 동결 액션으로만, 그 밖은 동결 액션으로 못 넘는다"""
    frm, to = pair
    actor = create_user(f"so-chan-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    so_id = _make(pair)
    good = _kwargs_for(pair, actor)
    with pytest.raises(AppError) as auto:
        _transition(so_id, to, **{**good, "automatic": True})
    assert auto.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    with pytest.raises(AppError) as flipped:
        _transition(so_id, to, **{**good, "via_freeze_action": pair not in FREEZE})
    assert flipped.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id) == frm


@pytest.mark.parametrize("pair", sorted(AUTO))
def test_the_shipping_convergence_edges_reject_the_human_channel(pair: tuple[str, str]) -> None:
    """선적 수렴 2엣지(CONFIRMED↔IN_SHIPMENT)는 자동 통로로만 — 사람 통로(automatic=False)·동결 액션 통로는 409이고 상태 무변(공개 API로 요청 불가)"""
    frm, to = pair
    actor = create_user(f"so-auto-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    so_id = _make(pair)
    for kwargs in (
        {"automatic": False, "via_freeze_action": False},
        {"automatic": True, "via_freeze_action": True},
    ):
        with pytest.raises(AppError) as caught:
            _transition(so_id, to, actor_user_id=actor, reason=None, **kwargs)
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id) == frm


@pytest.mark.parametrize("to", ["CANCELLED", "ON_HOLD"])
def test_cancel_and_hold_require_a_reason(to: str) -> None:
    """취소·보류 도달은 사유 필수 — 없으면(공백 포함) 422 REASON_REQUIRED이고 상태 무변 · 500자 초과 422"""
    so_id = raw_so("RECEIVED")
    actor = create_user(f"so-reason-{to}@example.com", roles=(RoleCode.TRADE,))
    for reason in (None, "  "):
        with pytest.raises(AppError) as caught:
            _transition(so_id, to, actor_user_id=actor, reason=reason, automatic=False)
        assert caught.value.code == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    with pytest.raises(AppError) as long:
        _transition(so_id, to, actor_user_id=actor, reason="가" * 501, automatic=False)
    assert long.value.code == "COMMON.VALIDATION.INVALID_FIELD"
    assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id) == "RECEIVED"
    assert (
        _transition(so_id, to, actor_user_id=actor, reason="가" * 500, automatic=False)
        == "RECEIVED"
    )


def test_the_freeze_action_sets_confirmed_at_and_needs_a_complete_document() -> None:
    """동결 액션(RECEIVED→CONFIRMED)은 같은 flush에 confirmed_at을 채운다 · 결제조건·환율이 빈 SO는 동결 완결성 CHECK가 거부한다(서비스 선검사는 `confirm_sales_order` — PR-12a)"""
    actor = create_user("so-freeze@example.com", roles=(RoleCode.TRADE,))
    ok = raw_so("RECEIVED")
    assert (
        _transition(
            ok,
            "CONFIRMED",
            actor_user_id=actor,
            reason=None,
            automatic=False,
            via_freeze_action=True,
        )
        == "RECEIVED"
    )
    assert _scalar("SELECT confirmed_at IS NOT NULL FROM sales_orders WHERE id = :i", i=ok) is True
    incomplete = raw_so("RECEIVED", complete=False)
    with pytest.raises(Exception) as caught:
        _transition(
            incomplete,
            "CONFIRMED",
            actor_user_id=actor,
            reason=None,
            automatic=False,
            via_freeze_action=True,
        )
    assert "ck_sales_orders_frozen_complete" in str(caught.value)
    assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=incomplete) == "RECEIVED"


def test_a_confirmed_hold_resumes_only_to_confirmed_and_keeps_confirmed_at() -> None:
    """확정된 SO의 보류→재개는 ON_HOLD→CONFIRMED(엣지 7) — 확정 시각은 그대로(재확정·해제 없음)이고 RECEIVED로는 DB 결속 CHECK가 막는다"""
    actor = create_user("so-resume@example.com", roles=(RoleCode.TRADE,))
    so_id = raw_so("CONFIRMED")
    at = _scalar("SELECT confirmed_at FROM sales_orders WHERE id = :i", i=so_id)
    _transition(so_id, "ON_HOLD", actor_user_id=actor, reason="보류", automatic=False)
    with pytest.raises(Exception) as caught:
        _transition(so_id, "RECEIVED", actor_user_id=actor, reason=None, automatic=False)
    assert "ck_sales_orders_confirmed_at_consistent" in str(caught.value)
    _transition(so_id, "CONFIRMED", actor_user_id=actor, reason=None, automatic=False)
    assert _scalar("SELECT confirmed_at FROM sales_orders WHERE id = :i", i=so_id) == at


def test_reserved_states_have_no_in_or_out_edges_and_no_rows_reach_them_through_the_channel() -> (
    None
):
    """RESERVED 4상태에는 in/out 엣지가 0이고 어떤 통로로도 도달 못 한다 — 이력에도 그 값을 가진 행이 없다"""
    reserved = RESERVED[KIND]
    for a, b in HUMAN | AUTO:
        assert a not in reserved and b not in reserved
    actor = create_user("so-reserved@example.com", roles=(RoleCode.TRADE,))
    so_id = raw_so("CONFIRMED")
    for target in reserved:
        with pytest.raises(AppError) as caught:
            _transition(so_id, target, actor_user_id=actor, reason=None, automatic=False)
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED", target
    assert (
        _scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE to_status = ANY(:r)",
            r=sorted(reserved),
        )
        == 0
    )


def test_history_and_event_counts_match_the_transitions_taken() -> None:
    """이력 행 수 대사 — 접수→보류→재개→취소 시나리오 후 이력 4행(탄생+3전이)·이벤트 status_changed 3건, 최신 to_status == 문서 status"""
    actor = create_user("so-count@example.com", roles=(RoleCode.TRADE,))
    so_id = raw_so("RECEIVED")
    for to, reason in (("ON_HOLD", "보류"), ("RECEIVED", None), ("CANCELLED", "취소")):
        _transition(so_id, to, actor_user_id=actor, reason=reason, automatic=False)
    rows = _log_rows(so_id)
    assert len(rows) == 4 and _events(so_id) == 3
    assert rows[-1][1] == _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so_id)
    assert all(rows[i][1] == rows[i + 1][0] for i in range(len(rows) - 1))  # 연쇄 from == 직전 to
    assert {("RECEIVED", "CONFIRMED")} == FREEZE
