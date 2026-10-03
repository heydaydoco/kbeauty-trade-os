"""J. 확정 동시성 — **여신 결정적 경합**·더블클릭·교차 데드락 0·잠금 순서·롤백·55P03 (S3-1 PR-12a / DoD ③ / design-E E3·E4 (f) J / ADR-0059·0064·0070).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 UoW(세션)를 연다. 커밋을 동반하므로 병렬(xdist)로 돌리지 않는다.
**변이 확인**: 거래처 잠금(`lock_buyer_for_credit`의 `with_for_update`)을 제거하면 결정적 경합 테스트가 실패해야 한다(PROGRESS에 기록 — GC-F1 유형).
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, text

from app.core.db.session import engine
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.credit import evaluation
from app.modules.identity.models import RoleCode
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.approvals import approval_row, count, decide, make_user, set_credit_limit
from tests.factories.confirm import (
    approvals_of,
    confirm,
    confirm_service,
    ensure_approval_line,
    evaluations,
    ready_so,
    ready_so_for,
    request_service,
    scalar,
    so_row,
    status_log,
)
from tests.factories.gates import set_policy, set_readiness
from tests.factories.payments import actor as payer
from tests.factories.payments import receipt_body
from tests.factories.trade import (
    create_buyer,
    create_pi_via_api,
    create_priced_sku,
    create_so_from_pi_via_api,
    idem,
    issued_quotation,
    logged_in,
    unique,
)
from tests.support.concurrency import Outcome, run_concurrently

pytestmark = pytest.mark.group_j

TRADE = RoleCode.TRADE
BLOCKED = "TRADE_CHAIN.CONFIRM.GATE_BLOCKED"


def _trade() -> Any:
    return make_user(TRADE)


def _app_errors(outcomes: list[Outcome]) -> list[AppError]:
    """실패는 전부 업무 에러(AppError)여야 한다 — 교착(40P01)·500·미분류 예외는 실패다."""
    errors = [o.error for o in outcomes if o.error is not None]
    assert all(isinstance(e, AppError) for e in errors), [type(e) for e in errors]
    return [e for e in errors if isinstance(e, AppError)]


def _hold_back_credit_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    """평가 직후 이음새를 Barrier(2, 1.5s)에 묶는다 — 거래처 잠금이 있으면 뒤 스레드는 평가 **전에** 막혀 앞 스레드의 대기가 타임아웃으로 풀린다(직렬화).
    잠금이 없으면 두 스레드가 같은 순간 평가해 둘 다 '한도 이내'로 본다."""
    barrier = threading.Barrier(2, timeout=1.5)

    def hook() -> None:
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()

    monkeypatch.setattr(evaluation, "_after_evaluate_hook", hook)


# ══ 여신 결정적 경합 ═══════════════════════════════════════════════════════════════════


@pytest.mark.golden
def test_two_concurrent_confirmations_cannot_both_fit_under_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GC-F2 — **결정적 경합** — 한도 1,000·같은 거래처 SO 2건(각 600): 거래처 잠금이 직렬화해 정확히 1건 200(확정·WITHIN_LIMIT)·1건 GATE_BLOCKED(CREDIT 승인 필요) —
    `confirmed_at` 있는 SO는 정확히 1건. (잠금을 제거하면 둘 다 통과해 이 테스트가 실패해야 한다 — 변이 확인)"""
    ensure_approval_line()
    first = ready_so(limit=1_000, price=600, quantity=1)
    second = ready_so_for(first["buyer"], price=600, quantity=1)
    _hold_back_credit_evaluation(monkeypatch)
    actors = [_trade(), _trade()]
    orders = [first["id"], second["id"]]

    outcomes = run_concurrently(lambda i: confirm_service(actors[i], orders[i]), workers=2)
    assert sum(o.ok for o in outcomes) == 1, [o.error for o in outcomes]
    [error] = _app_errors(outcomes)
    assert error.code == ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED
    assert error.detail["needs_approval"] is True
    assert count("sales_orders", "confirmed_at IS NOT NULL") == 1
    winner = next(o for o in outcomes if o.ok)
    assert winner.value[1]["sales_order"]["credit_verdict"] == "WITHIN_LIMIT"
    loser = orders[1 - winner.index]
    assert so_row(loser)["status"] == "RECEIVED" and len(evaluations(loser, "BLOCKED")) == 1


@pytest.mark.parametrize("round_no", range(10))
def test_ten_natural_races_on_fresh_buyers_always_admit_exactly_one(round_no: int) -> None:
    """자연 경합 10회(매회 새 거래처 — 플래키 방지, 배리어 없음) — 매번 정확히 1건만 확정되고 나머지는 승인 필요로 막힌다"""
    ensure_approval_line()
    first = ready_so(limit=1_000, price=600, quantity=1)
    second = ready_so_for(first["buyer"], price=600, quantity=1)
    actors, orders = [_trade(), _trade()], [first["id"], second["id"]]
    outcomes = run_concurrently(lambda i: confirm_service(actors[i], orders[i]), workers=2)
    assert sum(o.ok for o in outcomes) == 1, (round_no, [o.error for o in outcomes])
    [error] = _app_errors(outcomes)
    assert error.code == ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED
    assert count("sales_orders", "confirmed_at IS NOT NULL") == 1


def test_three_concurrent_confirmations_of_400_each_admit_exactly_two() -> None:
    """한도 1,000에 SO 3건(각 400) 동시 확정 → 정확히 2건 통과(800 ≤ 1,000) · 1건은 승인 필요(1,200 > 1,000)"""
    ensure_approval_line()
    a = ready_so(limit=1_000, price=400, quantity=1)
    orders = [a["id"]] + [ready_so_for(a["buyer"], price=400, quantity=1)["id"] for _ in range(2)]
    actors = [_trade() for _ in range(3)]
    outcomes = run_concurrently(lambda i: confirm_service(actors[i], orders[i]), workers=3)
    assert sum(o.ok for o in outcomes) == 2, [o.error for o in outcomes]
    assert all(e.code == ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED for e in _app_errors(outcomes))
    assert count("sales_orders", "confirmed_at IS NOT NULL") == 2


def test_confirmations_for_different_buyers_do_not_wait_for_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """다른 거래처 2건 동시 확정은 서로 대기하지 않는다(잠금 범위 증명) — 둘 다 같은 순간 평가 이음새에 들어간다(같은 잠금이면 10초 배리어가 깨져 실패)"""
    ensure_approval_line()
    orders = [ready_so(limit=1_000_000, price=100, quantity=1)["id"] for _ in range(2)]
    barrier = threading.Barrier(2, timeout=10)
    monkeypatch.setattr(evaluation, "_after_evaluate_hook", lambda: barrier.wait())
    actors = [_trade(), _trade()]
    outcomes = run_concurrently(lambda i: confirm_service(actors[i], orders[i]), workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert count("sales_orders", "confirmed_at IS NOT NULL") == 2


def test_a_confirmation_holding_the_credit_lock_does_not_block_new_documents_for_the_buyer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비블록 증명 — 확정 트랜잭션이 거래처 잠금(NO KEY UPDATE)을 쥔 채 멈춰 있어도 같은 거래처의 새 SO 작성이 lock_timeout 안에 성공한다(KEY SHARE와 비충돌)"""
    so = ready_so(limit=1_000_000, price=100, quantity=1)
    inside, release = threading.Event(), threading.Event()

    def hook() -> None:
        inside.set()
        assert release.wait(timeout=20)

    monkeypatch.setattr(evaluation, "_after_evaluate_hook", hook)
    actor = _trade()
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = confirm_service(actor, so["id"])
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert inside.wait(timeout=20)
        extra = ready_so_for(so["buyer"], price=100, quantity=1)  # 잠금 보유 중 새 SO 작성
        assert extra["id"] != so["id"]
    finally:
        release.set()
        thread.join(timeout=30)
    assert "error" not in box, box.get("error")


# ══ 더블클릭 ═══════════════════════════════════════════════════════════════════════════


def test_the_same_key_sent_twice_at_once_confirms_once_and_both_see_the_same_result() -> None:
    """확정 더블클릭(같은 키 동시 2요청) — 멱등 행 잠금이 직렬화한다: 확정 1회·이력 확정 행 1개·증거 CONFIRMED 1행, 두 응답이 같다"""
    so = ready_so()
    actor = _trade()
    key = unique("dbl")
    version = scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])
    outcomes = run_concurrently(
        lambda _: confirm_service(actor, so["id"], version=version, key=key), workers=2
    )
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert len(status_log(so["id"])) == 2 and len(evaluations(so["id"], "CONFIRMED")) == 1


def test_two_clicks_with_different_keys_confirm_once_and_the_other_is_rejected() -> None:
    """다른 키의 동시 2요청 — SO 행 잠금이 직렬화해 정확히 1건 성공, 다른 쪽은 409(version 충돌 또는 이미 확정)이며 이력·증거는 1회분"""
    so = ready_so()
    actors = [_trade(), _trade()]
    version = scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])
    outcomes = run_concurrently(
        lambda i: confirm_service(actors[i], so["id"], version=version), workers=2
    )
    assert sum(o.ok for o in outcomes) == 1, [o.error for o in outcomes]
    [error] = _app_errors(outcomes)
    assert error.status_code == 409
    assert len(status_log(so["id"])) == 2 and len(evaluations(so["id"], "CONFIRMED")) == 1


def test_two_concurrent_confirmations_of_one_order_with_one_approval_consume_it_once() -> None:
    """동시 확정 2요청+승인 1건 — 정확히 1건 CONSUMED·확정 1회(승인 이중 소비 불가)"""
    ensure_approval_line()
    so = ready_so(limit=3_000)
    requester, approver = _trade(), _trade()
    _, body = request_service(requester, so["id"])
    decide(body["id"], approver, "APPROVE")
    actors = [_trade(), _trade()]
    version = scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])
    outcomes = run_concurrently(
        lambda i: confirm_service(actors[i], so["id"], version=version), workers=2
    )
    assert sum(o.ok for o in outcomes) == 1, [o.error for o in outcomes]
    _app_errors(outcomes)
    assert approval_row(body["id"])["status"] == "CONSUMED"
    assert count("approval_events", "to_status = 'CONSUMED'") == 1
    assert len(status_log(so["id"])) == 2


# ══ 롤백 — 중간 실패는 소비까지 되돌린다 ═══════════════════════════════════════════════════


class _PortFailure(RuntimeError):
    pass


def test_a_failure_after_the_approval_is_consumed_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """확정 중간 실패(승인 소비 **뒤** 할당 포트 예외) — 전체 롤백: SO RECEIVED·증적 NULL·상태이력 탄생 1행·CONFIRMED 증거 0·**승인은 여전히 APPROVED**(소비 되돌림)·QT도 ISSUED 그대로"""
    from app.modules.trade_chain import confirm as confirm_module

    ensure_approval_line()
    with logged_in(TRADE) as client:
        buyer = create_buyer()
        sku = create_priced_sku(amount=1000)
        qt = issued_quotation(client, buyer, [sku], quantity=10)
        pi = create_pi_via_api(client, qt)
        so = create_so_from_pi_via_api(client, pi)
    set_readiness(sku, "GREEN")
    set_policy("pi_advance_gate_mode", "OFF")
    set_credit_limit(buyer, 5_000)  # 총액 10,000 → 초과분 5,000
    requester, approver = _trade(), _trade()
    _, requested = request_service(requester, so["id"])
    decide(requested["id"], approver, "APPROVE")

    class Failing:
        def on_confirmed(self, session: Any, order: Any) -> Any:
            raise _PortFailure("할당 포트 실패 주입")

        def on_cancelled(self, session: Any, order: Any) -> Any:
            raise AssertionError

    monkeypatch.setattr(confirm_module, "get_allocation_port", lambda: Failing())
    with pytest.raises(_PortFailure):
        confirm_service(_trade(), so["id"])
    row = so_row(so["id"])
    assert row["status"] == "RECEIVED" and row["confirmed_at"] is None
    assert (row["credit_verdict"], row["credit_approval_id"], row["pi_gate_verdict"]) == (
        None,
        None,
        None,
    )
    assert len(status_log(so["id"])) == 1 and evaluations(so["id"]) == []
    assert approval_row(requested["id"])["status"] == "APPROVED"
    assert count("approval_events", "to_status = 'CONSUMED'") == 0
    assert scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "ISSUED"
    # 같은 상태에서 포트가 정상이면 확정된다(실패가 상태를 오염시키지 않았다)
    monkeypatch.undo()
    status, body = confirm_service(_trade(), so["id"])
    assert status == 200 and body["sales_order"]["credit_verdict"] == "APPROVED"
    assert scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == "CONVERTED"


def test_a_gate_blocked_attempt_commits_its_evidence_even_though_the_response_is_an_error() -> None:
    """차단 응답(409)이 예외로 나가도 BLOCKED 증거·우회 시도 audit이 **커밋돼 남는다**(11a 인계 ⑦ — 롤백되면 증거가 사라진다)"""
    ensure_approval_line()
    so = ready_so(limit=3_000)
    with pytest.raises(AppError) as caught:
        confirm_service(_trade(), so["id"])
    assert caught.value.code == ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED
    assert len(evaluations(so["id"], "BLOCKED")) == 1
    assert count("audit_log", "action = 'approvals.approval.bypass_blocked'") == 1


def test_the_blocked_attempt_keeps_the_idempotency_key_usable_after_the_approval_arrives() -> None:
    """차단된 시도의 키는 소비되지 않는다 — 승인을 받은 뒤 **같은 키**로 재확정이 성공한다(거부를 최초 결과로 재생하면 영원히 막힌다)"""
    ensure_approval_line()
    so = ready_so(limit=3_000)
    actor, key = _trade(), unique("retry")
    version = scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])
    with pytest.raises(AppError):
        confirm_service(actor, so["id"], version=version, key=key)
    _, requested = request_service(actor, so["id"], version=version)
    decide(requested["id"], _trade(), "APPROVE")
    status, body = confirm_service(actor, so["id"], version=version, key=key)
    assert status == 200 and body["sales_order"]["credit_approval_id"] == requested["id"]


# ══ 55P03 — 잠금 대기 초과는 409 LOCK_BUSY, 같은 키 재시도 성공 ═══════════════════════════


def test_a_lock_timeout_on_the_buyer_row_is_a_409_and_the_same_key_succeeds_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """55P03 실접촉 — 다른 연결이 거래처 행을 잡은 채 대기(테스트 전용 lock_timeout 300ms)하는 확정 요청은 **500이 아니라 409 `COMMON.CONCURRENCY.LOCK_BUSY`** ·
    잠금 해제 뒤 **같은 Idempotency-Key로 재시도하면 성공**한다(멱등 claim 행도 같은 트랜잭션이라 롤백됨)"""
    from app.modules.trade_chain import confirm as confirm_module

    so = ready_so()
    key = idem()
    real = confirm_module.lock_buyer_for_credit

    def short_timeout(session: Any, partner_id: int) -> Any:
        session.execute(text("SET LOCAL lock_timeout = '300ms'"))
        return real(session, partner_id)

    monkeypatch.setattr(confirm_module, "lock_buyer_for_credit", short_timeout)
    holder = engine.connect()
    holder_tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM partners WHERE id = :p FOR UPDATE"), {"p": so["buyer"]})
        with logged_in(TRADE) as client:
            busy = confirm(client, so["id"], headers=key)
            assert busy.status_code == 409, busy.text
            assert busy.json()["error"]["code"] == "COMMON.CONCURRENCY.LOCK_BUSY"
            assert so_row(so["id"])["status"] == "RECEIVED"
            holder_tx.rollback()
            holder.close()
            holder = None  # type: ignore[assignment]
            again = confirm(client, so["id"], headers=key)
    finally:
        if holder is not None:
            holder_tx.rollback()
            holder.close()
    assert again.status_code == 200, again.text


# ══ 잠금 순서 — 계측 ═══════════════════════════════════════════════════════════════════════

_ORDER_NAMES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "quotations": "quotations",
    "proforma_invoices": "proforma_invoices",
    "sales_orders": "sales_orders",
    "approvals": "approvals",
    # 라인 표는 전역 순서 (8) `lines` — 원천 라인 잠금(참조 수주의 수량 증가)도 여기 속한다
    "quotation_lines": "lines",
    "proforma_invoice_lines": "lines",
    "sales_order_lines": "lines",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _first_lock_sequence(work: Callable[[], object]) -> list[str]:
    """work가 보낸 SQL의 행 잠금(`FOR …`)의 **첫 접촉 순서**(LOCK_ORDER 대상 테이블만)."""
    seen: list[str] = []

    def listener(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if _LOCK_CLAUSE.search(statement):
            found = _FROM.search(statement)
            name = _ORDER_NAMES.get(found.group(1)) if found else None
            if name and name not in seen:
                seen.append(name)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return seen


def _chain_so(
    client: Any, *, limit: int | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """PI 참조 수주(QT→PI→SO 사슬) — 확정 가능 상태(준비도 GREEN·PI 게이트 OFF). (QT, PI, SO)를 돌려준다."""
    buyer = create_buyer()
    sku = create_priced_sku(amount=1000)
    qt = issued_quotation(client, buyer, [sku], quantity=10)
    pi = create_pi_via_api(client, qt)
    so = create_so_from_pi_via_api(client, pi)
    set_readiness(sku, "GREEN")
    set_policy("pi_advance_gate_mode", "OFF")
    if limit is not None:
        set_credit_limit(buyer, limit)
    so["buyer"] = buyer
    return qt, pi, so


def test_confirm_locks_buyer_then_chain_then_order_then_approvals_in_the_global_order() -> None:
    """전역 LOCK_ORDER 준수(계측) — 확정이 실제로 보낸 잠금 SQL의 첫 접촉 순서가 partners → quotations → proforma_invoices → sales_orders → approvals다.
    (authoritative 평가가 거래처 잠금을 다시 잡더라도 **거래처가 SO보다 먼저**여야 한다 — 11a 인계 ⑤)"""
    ensure_approval_line()
    with logged_in(TRADE) as client:
        _, _, so = _chain_so(client, limit=5_000)
    requester, approver = _trade(), _trade()
    _, requested = request_service(requester, so["id"])
    decide(requested["id"], approver, "APPROVE")
    seen = _first_lock_sequence(lambda: confirm_service(_trade(), so["id"]))
    assert seen[:5] == [
        "partners",
        "quotations",
        "proforma_invoices",
        "sales_orders",
        "approvals",
    ], seen
    indexes = [LOCK_ORDER.index(name) for name in seen if name in LOCK_ORDER]
    assert indexes == sorted(indexes), seen


def test_the_approval_request_and_the_decision_follow_the_same_lock_order_as_the_confirmation() -> (
    None
):
    """승인 요청도 거래처 → QT → PI → SO → approvals 순이다(확정과 같은 순서라 교차 교착이 없다)"""
    ensure_approval_line()
    with logged_in(TRADE) as client:
        _, _, so = _chain_so(client, limit=5_000)
    seen = _first_lock_sequence(lambda: request_service(_trade(), so["id"]))
    indexes = [LOCK_ORDER.index(name) for name in seen if name in LOCK_ORDER]
    assert seen[0] == "partners" and indexes == sorted(indexes), seen
    assert "sales_orders" in seen and "approvals" in seen, seen


def test_the_lock_probe_flags_a_reversed_order() -> None:
    """공회전 방지 — 계측이 잠금을 실제로 기록하고, 뒤집힌 순서는 위반으로 판정된다"""
    so = ready_so()
    seen = _first_lock_sequence(lambda: confirm_service(_trade(), so["id"]))
    assert seen and seen[0] == "partners" and "sales_orders" in seen, seen
    assert [LOCK_ORDER.index(n) for n in seen] == sorted(LOCK_ORDER.index(n) for n in seen)
    reversed_order = list(reversed(seen))
    assert [LOCK_ORDER.index(n) for n in reversed_order] != sorted(
        LOCK_ORDER.index(n) for n in reversed_order
    )


# ══ 교차 반복 — 확정 × 승인 결정 × 입금 기록 ═══════════════════════════════════════════════


def test_confirm_decide_and_payment_in_all_interleavings_never_deadlock() -> None:
    """**확정 × 승인 결정 × 입금 기록 교차 30회** — 매 반복 3연산을 동시에 출발시켜도 데드락(40P01)·500이 0건이다. 결과 불변식: 확정되면 승인은 CONSUMED(APPROVED 판정)이고
    확정 못 했으면(결정 전 도착) 승인은 REQUESTED·APPROVED이며 SO는 접수 그대로 — 어느 쪽이든 입금은 기록된다. (확정이 SO→거래처로 잠그면 결정(거래처→SO)과 교착한다 — 변이 표적)"""
    ensure_approval_line()
    confirmed = 0
    for _ in range(30):
        with logged_in(TRADE) as client:
            _qt, pi, so = _chain_so(client, limit=5_000)
        requester, approver, confirmer = _trade(), _trade(), _trade()
        _, requested = request_service(requester, so["id"])
        receiver = payer(TRADE)

        def worker(
            i: int,
            _so: dict[str, Any] = so,
            _pi: dict[str, Any] = pi,
            _req: dict[str, Any] = requested,
            _approver: Any = approver,
            _confirmer: Any = confirmer,
            _receiver: Any = receiver,
        ) -> Any:
            if i == 0:
                return confirm_service(_confirmer, _so["id"])
            if i == 1:
                return decide(_req["id"], _approver, "APPROVE")
            from app.modules.trade_chain import payment_flow

            return payment_flow.record_receipt(
                actor=_receiver,
                idempotency_key=unique("pay"),
                pi_id=_pi["id"],
                payload=receipt_body("5.00"),
            )

        outcomes = run_concurrently(worker, workers=3)
        errors = _app_errors(outcomes)
        assert all(
            e.code
            in (ErrorCode.TRADE_CHAIN_CONFIRM_GATE_BLOCKED, ErrorCode.APPROVALS_APPROVAL_STALE)
            for e in errors
        ), [e.code for e in errors]
        assert outcomes[2].ok, outcomes[2].error  # 입금은 항상 기록된다
        row = so_row(so["id"])
        status = approval_row(requested["id"])["status"]
        if outcomes[0].ok:
            confirmed += 1
            assert row["status"] == "CONFIRMED" and row["credit_verdict"] == "APPROVED"
            assert status == "CONSUMED" and row["credit_approval_id"] == requested["id"]
        else:
            assert row["status"] == "RECEIVED" and status in ("REQUESTED", "APPROVED")
        assert len(approvals_of(so["id"])) == 1
    assert 0 <= confirmed <= 30  # 결정이 확정보다 먼저/나중인 양쪽이 모두 정상 결과다


def test_a_reference_order_quantity_increase_voids_the_approval_before_locking_source_lines() -> (
    None
):
    """참조 수주의 수량 증가(라인 수정)는 SO → **approvals(승인 무효화)** → 원천 라인(PI 라인 `FOR UPDATE`) 순으로 잠근다 — 전역 LOCK_ORDER (5)→(7)→(8). 승인 무효화가
    원천 라인 잠금보다 뒤였던 역순(8→7)을 고친 회귀 시험(실측 SQL 순서)"""
    ensure_approval_line()
    with logged_in(TRADE) as client:
        _, _, so = _chain_so(client, limit=3_000)
        (line,) = client.get(f"/api/v1/sales-orders/{so['id']}").json()["lines"]
        version = scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])
        down = client.patch(
            f"/api/v1/sales-orders/{so['id']}/lines/{line['id']}",
            json={"version": version, "quantity": 5},
        )
        assert down.status_code == 200, down.text
        _, requested = request_service(_trade(), so["id"])  # 열린 승인 1건
        version = scalar("SELECT version FROM sales_orders WHERE id = :i", i=so["id"])
        seen = _first_lock_sequence(
            lambda: client.patch(
                f"/api/v1/sales-orders/{so['id']}/lines/{line['id']}",
                json={"version": version, "quantity": 6},
            )
        )
    assert approval_row(requested["id"])["status"] == "VOIDED"
    assert seen[:3] == ["sales_orders", "approvals", "lines"], seen
    assert [LOCK_ORDER.index(n) for n in seen if n in LOCK_ORDER] == sorted(
        LOCK_ORDER.index(n) for n in seen if n in LOCK_ORDER
    )
