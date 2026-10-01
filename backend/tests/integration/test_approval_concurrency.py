"""J. 승인 동시성 — 이중 결정·이중 소비·결정 vs 무효·동시 요청·잠금 순서·결정 중 자격 상실 (S3-1 PR-9a / ADR-0059·0060 / design-C C6·C8, design-E E3).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 UoW(세션)를 연다. 커밋을 동반하므로
  병렬(pytest-xdist)로 돌리지 않는다. 결과 불변식(정확히 1건 성공·이력 1행·교착 0·500 0)을 본다.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, text

from app.core.db.session import engine, owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.approvals import delegations, lines, service
from app.modules.approvals.machine import ConsumeOutcome, VoidReasonCode
from app.modules.credit import evaluation
from app.modules.credit.evaluation import CreditVerdict, evaluate_credit
from app.modules.credit.locking import lock_buyer_for_credit
from app.modules.identity.models import RoleCode
from app.modules.sales_orders.models import SalesOrder
from tests.factories.approvals import (
    TYPE,
    add_line,
    approval_row,
    approved_for,
    count,
    credit_so,
    decide,
    events_of,
    make_user,
    request,
    set_credit_limit,
)
from tests.factories.trade import create_buyer, create_direct_so, create_priced_sku, unique
from tests.support.concurrency import Outcome, run_concurrently

pytestmark = pytest.mark.group_j


def _pending() -> tuple[dict[str, Any], int, Any]:
    so = credit_so()
    if count("approval_lines") == 0:
        add_line(0, role="TRADE")
    requester = make_user(RoleCode.TRADE)
    return so, int(request(so["id"], requester).approval.id), requester


def _errors(outcomes: list[Outcome]) -> list[AppError]:
    errs = [o.error for o in outcomes if o.error is not None]
    assert all(isinstance(e, AppError) for e in errs), [
        type(e) for e in errs
    ]  # 500·교착(DBAPIError)은 실패다
    return [e for e in errs if isinstance(e, AppError)]


# ── 결정 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "verbs", [("APPROVE", "APPROVE"), ("APPROVE", "REJECT")], ids=["승인×승인", "승인×반려"]
)
def test_two_approvers_deciding_at_once_yield_exactly_one_decision(verbs: tuple[str, str]) -> None:
    """두 결재자의 동시 결정은 승인 행 잠금이 직렬화한다 — 정확히 1건 성공·나머지는 409 NOT_ALLOWED·이력은 결정 1행"""
    _, approval_id, _ = _pending()
    approvers = [make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)]

    def worker(i: int) -> dict[str, Any]:
        return decide(approval_id, approvers[i], verbs[i], reason="사유", version=1)

    outcomes = run_concurrently(worker, workers=2)
    assert sum(o.ok for o in outcomes) == 1, [o.error for o in outcomes]
    [error] = _errors(outcomes)
    assert error.code == ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED
    assert len(events_of(approval_id)) == 2  # REQUESTED + 결정 1행
    assert approval_row(approval_id)["status"] in ("APPROVED", "REJECTED")
    assert count("alerts", "dedup_key LIKE :p", p=f"approval:{approval_id}:%:%") >= 1


def test_the_same_idempotency_key_sent_twice_at_once_decides_once() -> None:
    """같은 키의 동시 2요청은 멱등 행 잠금으로 직렬화된다 — 결정 1건, 두 응답이 같다(뒤 요청은 저장된 최초 결과를 본다)"""
    _, approval_id, _ = _pending()
    approver = make_user(RoleCode.TRADE)
    key = unique("same-key")

    def worker(_: int) -> dict[str, Any]:
        return decide(approval_id, approver, "APPROVE", version=1, key=key)

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert len(events_of(approval_id)) == 2


def test_a_deactivated_approver_loses_authority_even_mid_decision() -> None:
    """결정 트랜잭션이 승인 행을 잠근 **뒤** 승인자가 비활성화·커밋되면 결정은 거부된다(잠금 후 자격 재계산 — TOCTOU)"""
    _, approval_id, _ = _pending()
    approver = make_user(RoleCode.TRADE)
    locked, deactivated = threading.Event(), threading.Event()
    real = service.decision_authority

    calls = {"n": 0}

    def gated(session: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:  # 잠금 전 무잠금 판정 — 통과시킨다(권한은 잠금 뒤 DB에서 한 번 더 본다)
            return real(session, **kwargs)
        locked.set()
        assert deactivated.wait(timeout=10)
        return real(session, **kwargs)

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = decide(approval_id, approver, "APPROVE", version=1)
        except BaseException as exc:
            box["error"] = exc

    service.decision_authority = gated  # type: ignore[assignment]
    try:
        thread = threading.Thread(target=run)
        thread.start()
        assert locked.wait(timeout=10)
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET is_active = false WHERE id = :i"), {"i": approver.id}
            )
        deactivated.set()
        thread.join(timeout=15)
    finally:
        service.decision_authority = real  # type: ignore[assignment]
    error = box.get("error")
    assert isinstance(error, AppError) and error.code == ErrorCode.APPROVALS_DECISION_NOT_APPROVER
    assert approval_row(approval_id)["status"] == "REQUESTED"


# ── 소비 ─────────────────────────────────────────────────────────────────────


def test_concurrent_consumption_spends_the_approval_exactly_once() -> None:
    """같은 승인으로 동시 확정 2요청 — 정확히 1건 CONSUMED, 다른 쪽은 BLOCKED(REQUIRED)(1회 소비)"""
    so = credit_so()
    approval_id, _, approver = approved_for(so)
    actors = [make_user(RoleCode.TRADE), make_user(RoleCode.ADMIN)]

    def worker(i: int) -> Any:
        with unit_of_work() as uow:
            return service.consume_approval(
                uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=actors[i].id
            )

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    results = [o.value for o in outcomes]
    assert sorted(r.outcome for r in results) == [ConsumeOutcome.BLOCKED, ConsumeOutcome.CONSUMED]
    blocked = next(r for r in results if r.outcome is ConsumeOutcome.BLOCKED)
    assert blocked.error is not None and blocked.error.code == ErrorCode.APPROVALS_APPROVAL_REQUIRED
    assert approval_row(approval_id)["status"] == "CONSUMED"
    assert [e["to_status"] for e in events_of(approval_id)].count("CONSUMED") == 1
    assert approver.id


def test_approving_races_with_a_target_edit_void_without_deadlock_or_500() -> None:
    """'승인'과 'SO 수정(void_for_target)'이 동시에 — 교착·500 없이 [승인→무효] 또는 [무효→승인 불가] 중 일관된 결과이고 최종은 VOIDED"""
    for _ in range(6):
        so, approval_id, _ = _pending()
        approver, editor = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)

        def worker(
            i: int,
            _so: dict[str, Any] = so,
            _id: int = approval_id,
            _a: Any = approver,
            _e: Any = editor,
        ) -> Any:
            if i == 0:
                return decide(_id, _a, "APPROVE", version=1)
            with unit_of_work() as uow:
                return service.void_for_target(
                    uow.session,
                    approval_type=TYPE,
                    target_id=_so["id"],
                    actor_user_id=_e.id,
                    reason_code=VoidReasonCode.TARGET_CHANGED,
                )

        outcomes = run_concurrently(worker, workers=2)
        _errors(outcomes)  # AppError 외(교착 40P01·500)는 즉시 실패
        row = approval_row(approval_id)
        sequence = [e["to_status"] for e in events_of(approval_id)]
        assert row["status"] == "VOIDED"
        assert sequence in (["REQUESTED", "APPROVED", "VOIDED"], ["REQUESTED", "VOIDED"]), sequence
        assert len([o for o in outcomes if o.ok]) >= 1


def test_concurrent_requests_for_the_same_snapshot_open_one_approval() -> None:
    """같은 대상의 동시 요청 2건(같은 스냅샷) — 대상·거래처 잠금이 직렬화해 활성 승인은 1건이고 두 번째는 기존 행을 돌려준다"""
    so = credit_so()
    add_line(0, role="TRADE")
    make_user(RoleCode.TRADE)
    requesters = [make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)]

    def worker(i: int) -> tuple[int, bool]:
        result = request(so["id"], requesters[i])
        return int(result.approval.id), result.created

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    ids = {o.value[0] for o in outcomes}
    assert len(ids) == 1
    assert sorted(o.value[1] for o in outcomes) == [False, True]
    assert count("approvals", "status IN ('REQUESTED','APPROVED')") == 1


# ── 설정 데이터 ──────────────────────────────────────────────────────────────


def test_concurrent_line_registration_with_the_same_threshold_keeps_one() -> None:
    """같은 (유형·통화·임계)의 동시 등록 — DB 유니크가 최종 방어해 1건만 성공하고 나머지는 409 DUPLICATE(500 아님)"""
    admin = make_user(RoleCode.ADMIN)

    def worker(_: int) -> Any:
        return lines.create_approval_line(
            actor=admin,
            idempotency_key=unique("line"),
            payload={
                "approval_type": TYPE,
                "threshold": Decimal("100"),
                "currency": "USD",
                "approver_role": "TRADE",
                "note": None,
            },
        )

    outcomes = run_concurrently(worker, workers=3)
    assert sum(o.ok for o in outcomes) == 1
    assert all(e.code == ErrorCode.APPROVALS_LINE_DUPLICATE for e in _errors(outcomes))
    assert count("approval_lines") == 1


def test_concurrent_delegation_registration_for_one_delegator_keeps_one() -> None:
    """같은 위임자·유형·역할의 동시 등록 — 위임자 행 잠금+겹침 검사로 정확히 1건(나머지 409 OVERLAP)"""
    from app.core.time import today_kst

    delegator, delegate = make_user(RoleCode.TRADE), make_user(RoleCode.CERT)

    def worker(_: int) -> Any:
        return delegations.create_delegation(
            actor=delegator,
            idempotency_key=unique("dlg"),
            payload={
                "delegate_user_id": delegate.id,
                "approval_type": TYPE,
                "delegated_role": "TRADE",
                "start_on": today_kst(),
                "end_on": today_kst(),
                "note": None,
                "delegator_user_id": None,
            },
        )

    outcomes = run_concurrently(worker, workers=3)
    assert sum(o.ok for o in outcomes) == 1
    assert all(e.code == ErrorCode.APPROVALS_DELEGATION_OVERLAP for e in _errors(outcomes))
    assert count("delegations") == 1


# ── 잠금 순서 ────────────────────────────────────────────────────────────────


def _record_locks(action: Callable[[], Any]) -> list[str]:
    """실행 중 앱 엔진이 보낸 행 잠금 SQL의 대상 테이블을 순서대로 기록한다(`FOR [NO KEY] UPDATE` 문장)."""
    seen: list[str] = []

    def before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if re.search(r"FOR (NO KEY )?UPDATE", statement):
            match = re.search(r"FROM (\w+)", statement)
            if match:
                seen.append(match.group(1))

    event.listen(engine, "before_cursor_execute", before)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    return seen


def _first(seen: list[str], table: str) -> int:
    assert table in seen, (table, seen)
    return seen.index(table)


def test_decide_consume_and_void_lock_buyer_then_order_then_approval() -> None:
    """전역 LOCK_ORDER 준수(계측) — 결정·소비·무효 모두 거래처 → SO → approvals 순(교착 방지). 실측 SQL 순서로 고정한다"""
    so, approval_id, requester = _pending()
    approver = make_user(RoleCode.TRADE)

    seen = _record_locks(lambda: decide(approval_id, approver, "APPROVE", version=1))
    assert _first(seen, "partners") < _first(seen, "sales_orders") < _first(seen, "approvals"), seen

    def consume() -> Any:
        with unit_of_work() as uow:
            return service.consume_approval(
                uow.session, approval_type=TYPE, target_id=so["id"], actor_user_id=approver.id
            )

    seen = _record_locks(consume)
    assert _first(seen, "partners") < _first(seen, "sales_orders") < _first(seen, "approvals"), seen

    so2, approval2, _ = _pending()

    def void() -> Any:
        with unit_of_work() as uow:
            return service.void_for_target(
                uow.session,
                approval_type=TYPE,
                target_id=so2["id"],
                actor_user_id=requester.id,
                reason_code=VoidReasonCode.TARGET_CANCELLED,
            )

    # void_for_target은 대상 잠금을 스스로 잡지 않는다(대상 편집·취소 트랜잭션이 이미 SO를 잠근 뒤 부른다) — approvals 잠금만 있다
    seen = _record_locks(void)
    assert seen == ["approvals"], seen
    assert approval2


def test_approvals_come_after_partners_and_sales_orders_in_the_global_lock_order() -> None:
    """상수 LOCK_ORDER에서 approvals는 partners·sales_orders 뒤, 라인·채번 앞이다(결정·소비 서비스가 따르는 순서의 정본)"""
    from app.modules.trade_docs.locking import LOCK_ORDER

    assert (
        LOCK_ORDER.index("partners")
        < LOCK_ORDER.index("sales_orders")
        < LOCK_ORDER.index("approvals")
    )
    assert (
        LOCK_ORDER.index("approvals")
        < LOCK_ORDER.index("lines")
        < LOCK_ORDER.index("doc_number_seq")
    )


def test_decide_consume_and_edit_in_all_interleavings_never_deadlock() -> None:
    """결정×소비×무효(편집) 교차 반복 — 데드락(40P01)·500 0건: 각 반복마다 세 연산을 동시에 출발시킨다"""
    for _ in range(12):
        so, approval_id, requester = _pending()
        approver = make_user(RoleCode.TRADE)

        def worker(
            i: int,
            _so: dict[str, Any] = so,
            _id: int = approval_id,
            _a: Any = approver,
            _r: Any = requester,
        ) -> Any:
            if i == 0:
                return decide(_id, _a, "APPROVE", version=1)
            if i == 1:
                with unit_of_work() as uow:
                    return service.consume_approval(
                        uow.session, approval_type=TYPE, target_id=_so["id"], actor_user_id=_a.id
                    )
            with unit_of_work() as uow:
                return service.void_for_target(
                    uow.session,
                    approval_type=TYPE,
                    target_id=_so["id"],
                    actor_user_id=_r.id,
                    reason_code=VoidReasonCode.TARGET_CHANGED,
                )

        outcomes = run_concurrently(worker, workers=3)
        _errors(outcomes)
        assert approval_row(approval_id)["status"] in (
            "APPROVED",
            "CONSUMED",
            "VOIDED",
            "REQUESTED",
        )


# ── 여신 직렬화 잠금(credit) ──────────────────────────────────────────────────


def _confirm_in(session: Any, so_id: int) -> None:
    """확정을 흉내 낸다(PR-12 확정 통로의 증적 대용) — 같은 트랜잭션에서 확정 시각·상태와 동결 완결 값(결제조건·Incoterms·환율)을 채운다."""
    session.execute(
        text(
            "UPDATE sales_orders SET status = 'CONFIRMED', confirmed_at = now(), credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE', payment_type = 'TT_DEFERRED',"
            " balance_anchor = 'RECEIPT_DATE', balance_days = 30, incoterm_code = 'EXW', incoterm_place = 'Seoul',"
            " incoterm_year = 2020, fx_rate = 1350, fx_rate_date = doc_date WHERE id = :i"
        ),
        {"i": so_id},
    )


def test_the_credit_lock_makes_two_concurrent_evaluations_see_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한도 100,000에 SO 60,000×2를 동시에 평가·확정 — 거래처 잠금이 직렬화해 정확히 1건 WITHIN_LIMIT·1건 EXCEEDED (결정적 경합)"""
    buyer = create_buyer()
    set_credit_limit(buyer, 100_000)
    sku = create_priced_sku(amount=60_000)
    orders = [create_direct_so(buyer, [sku], quantity=1, price_amount=60_000) for _ in range(2)]
    barrier = threading.Barrier(2, timeout=1.5)

    def hook() -> None:
        # 잠금이 있으면 뒤 스레드가 평가 전에 막혀 앞 스레드의 대기가 타임아웃으로 풀린다
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()

    monkeypatch.setattr(evaluation, "_after_evaluate_hook", hook)

    def worker(i: int) -> CreditVerdict:
        with unit_of_work() as uow:
            locked = lock_buyer_for_credit(uow.session, buyer)
            order = uow.session.get(SalesOrder, orders[i]["id"])
            assert order is not None
            verdict = evaluate_credit(uow.session, locked, order).verdict
            if verdict is CreditVerdict.WITHIN_LIMIT:
                _confirm_in(uow.session, orders[i]["id"])
            return verdict

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert sorted(o.value.value for o in outcomes) == ["EXCEEDED", "WITHIN_LIMIT"]
    assert count("sales_orders", "confirmed_at IS NOT NULL") == 1


def test_different_buyers_do_not_wait_for_each_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """다른 거래처의 평가는 서로 대기하지 않는다 — 잠금 범위 증명(둘 다 배리어를 통과해 동시에 평가에 들어간다)"""
    buyers = [create_buyer(), create_buyer()]
    for b in buyers:
        set_credit_limit(b, 1_000_000)
    sku = create_priced_sku(amount=100)
    orders = [create_direct_so(b, [sku], quantity=1, price_amount=100) for b in buyers]
    barrier = threading.Barrier(2, timeout=10)
    monkeypatch.setattr(evaluation, "_after_evaluate_hook", lambda: barrier.wait())

    def worker(i: int) -> str:
        with unit_of_work() as uow:
            locked = lock_buyer_for_credit(uow.session, buyers[i])
            order = uow.session.get(SalesOrder, orders[i]["id"])
            assert order is not None
            return evaluate_credit(uow.session, locked, order).verdict.value

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [
        o.error for o in outcomes
    ]  # 같은 잠금이면 배리어(10초)가 깨져 실패한다


def test_the_credit_lock_does_not_block_new_documents_for_the_same_buyer() -> None:
    """NO KEY UPDATE는 자식 INSERT의 FK 잠금(KEY SHARE)과 충돌하지 않는다 — 잠금을 쥔 채로 같은 거래처의 새 SO를 만들 수 있다(비블록 증명)"""
    buyer = create_buyer()
    set_credit_limit(buyer, 1_000_000)
    sku = create_priced_sku(amount=100)
    holding, release = threading.Event(), threading.Event()

    def holder() -> None:
        with unit_of_work() as uow:
            lock_buyer_for_credit(uow.session, buyer)
            holding.set()
            release.wait(timeout=20)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holding.wait(timeout=10)
        # 같은 거래처의 신규 SO 생성(거래처 FK 검사 = FOR KEY SHARE) — 5초 lock_timeout 안에 끝나야 한다
        created = create_direct_so(buyer, [sku], quantity=1, price_amount=100)
        assert created["id"]
    finally:
        release.set()
        thread.join(timeout=20)


def test_a_second_credit_lock_on_the_same_buyer_waits_for_the_first() -> None:
    """같은 거래처의 두 번째 `lock_buyer_for_credit`은 첫 잠금이 풀릴 때까지 기다린다(직렬화) — 풀린 뒤에만 획득"""
    buyer = create_buyer()
    first_has, release = threading.Event(), threading.Event()
    order: list[str] = []

    def first() -> None:
        with unit_of_work() as uow:
            lock_buyer_for_credit(uow.session, buyer)
            order.append("first-locked")
            first_has.set()
            release.wait(timeout=20)
            order.append("first-releasing")

    def second() -> None:
        with unit_of_work() as uow:
            lock_buyer_for_credit(uow.session, buyer)
            order.append("second-locked")

    t1 = threading.Thread(target=first)
    t1.start()
    assert first_has.wait(timeout=10)
    t2 = threading.Thread(target=second)
    t2.start()
    t2.join(timeout=1.0)
    assert t2.is_alive(), "두 번째 잠금이 기다리지 않고 통과했다"
    release.set()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert order == ["first-locked", "first-releasing", "second-locked"]
