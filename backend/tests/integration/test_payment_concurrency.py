"""J. 입금 동시성·원자성 — 더블클릭=1건·동시 두 입금 합계>due=1건·같은 입금 동시 역기록=1건·PI 잠금 직렬화·수렴 실패 완전 롤백·잠금 순서 (S3-1 PR-10a / ADR-0068 / design-E E7·E3).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다. 이 파일은 커밋을 동반한다.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import event, text

from app.core.db.session import engine, owner_engine
from app.core.errors.exceptions import AppError
from app.modules.trade_chain import payment_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.payments import (
    actor,
    advance_pi,
    ledger,
    pi_status,
    receipt_body,
    scalar,
    status_log,
)
from tests.factories.trade import unique
from tests.support.concurrency import run_concurrently

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]


class _Statements:
    """엔진이 실행한 SQL을 모은다 — 잠금 대기 중에 원장 INSERT가 이미 나갔는지(= 잠금 이전 쓰기) 판정한다."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, *args: object) -> None:
        self.seen.append(str(args[2]))

    def wrote_ledger(self) -> bool:
        return any(s.lstrip().upper().startswith("INSERT INTO PAYMENTS") for s in self.seen)


def _receipt(who: Any, pi: int, amount: str, *, key: str | None = None, **kw: Any) -> Any:
    return payment_flow.record_receipt(
        actor=who,
        idempotency_key=key or unique("conc"),
        pi_id=pi,
        payload=receipt_body(amount, **kw),
    )


def test_double_click_with_the_same_key_creates_exactly_one_payment() -> None:
    """같은 Idempotency-Key·같은 본문을 동시에 2번 → 원장 1건·두 응답 모두 같은 최초 결과(뒤늦은 요청은 재생)"""
    who = actor()
    pi = advance_pi()
    key = unique("dbl")
    body = receipt_body("100.00")

    def worker(_i: int) -> Any:
        return payment_flow.record_receipt(
            actor=who, idempotency_key=key, pi_id=pi, payload=dict(body)
        )

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert len(ledger(pi)) == 1 and pi_status(pi) == "PARTIALLY_PAID"
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'payments.receipt.recorded'") == 1
    assert scalar("SELECT count(*) FROM events WHERE event_type = 'payments.payment.recorded'") == 1


@pytest.mark.parametrize("round_no", range(4))
def test_two_concurrent_receipts_exceeding_due_in_total_yield_exactly_one(round_no: int) -> None:
    """같은 PI에 두 입금 동시(200.00+200.00, due 300.00) → PI 잠금 직렬화로 1건만 성공·다른 쪽은 EXCEEDS_DUE — 순입금 ≤ due 불변식"""
    who = actor()
    pi = advance_pi()

    def worker(i: int) -> Any:
        return _receipt(who, pi, "200.00", reference=f"R-{round_no}-{i}")

    outcomes = run_concurrently(worker, workers=2)
    ok = [o for o in outcomes if o.ok]
    bad = [o for o in outcomes if not o.ok]
    assert len(ok) == 1 and len(bad) == 1, [o.error for o in outcomes]
    assert isinstance(bad[0].error, AppError)
    assert bad[0].error.code == "PAYMENTS.PAYMENT.EXCEEDS_DUE"
    assert len(ledger(pi)) == 1
    assert scalar("SELECT SUM(received_amount) FROM payments WHERE pi_id = :p", p=pi) == 20_000
    assert pi_status(pi) == "PARTIALLY_PAID"
    assert len([r for r in status_log(pi) if r[0] is not None]) == 1  # 전이는 정확히 한 번


def test_two_concurrent_receipts_that_fit_both_land_and_the_status_converges_once_each() -> None:
    """합계가 due 이내인 두 입금(100+200) 동시 → 둘 다 성공·순입금 300.00·최종 PAID·상태 이력은 마지막 상태가 PAID로 정합(직렬화된 수렴)"""
    who = actor()
    pi = advance_pi()
    amounts = ["100.00", "200.00"]

    def worker(i: int) -> Any:
        return _receipt(who, pi, amounts[i], reference=f"FIT-{i}")

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert scalar("SELECT SUM(received_amount) FROM payments WHERE pi_id = :p", p=pi) == 30_000
    assert pi_status(pi) == "PAID"
    chain = [(a, b) for a, b, *_ in status_log(pi) if a is not None]
    assert chain[-1][1] == "PAID" and all(a != b for a, b in chain)


@pytest.mark.parametrize("round_no", range(3))
def test_two_concurrent_reversals_of_the_same_payment_yield_one(round_no: int) -> None:
    """같은 입금의 동시 역기록(서로 다른 키) → 1건 성공·1건 409 ALREADY_REVERSED — 한 입금은 한 번만 역기록"""
    who = actor()
    pi = advance_pi()
    _s, paid = _receipt(who, pi, "100.00")
    pid = paid["payment"]["id"]

    def worker(_i: int) -> Any:
        return payment_flow.reverse_payment(
            actor=who, idempotency_key=unique("rv"), payment_id=pid, reason="동시 정정"
        )

    outcomes = run_concurrently(worker, workers=2)
    assert len([o for o in outcomes if o.ok]) == 1, [o.error for o in outcomes]
    err = next(o.error for o in outcomes if not o.ok)
    assert isinstance(err, AppError) and err.code == "PAYMENTS.PAYMENT.ALREADY_REVERSED"
    assert [r["kind"] for r in ledger(pi)] == ["RECEIPT", "REVERSAL"]
    assert pi_status(pi) == "ISSUED"


def _run_behind_a_pi_lock_holder(pi: int, call: Any) -> Any:
    """다른 트랜잭션이 PI 행을 잡은 동안 `call`을 스레드로 시작해 (a) 끝나지 않고 대기하며 (b) 원장 INSERT를 아직 내보내지 않았음을 확인한 뒤, 잠금을 놓아 결과를 돌려준다."""
    holder = owner_engine.connect()
    tx = holder.begin()
    probe = _Statements()
    try:
        holder.execute(text("SELECT 1 FROM proforma_invoices WHERE id = :i FOR UPDATE"), {"i": pi})
        event.listen(engine, "before_cursor_execute", probe)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call)
            time.sleep(1.0)
            try:
                assert not future.done(), "PI 잠금을 무시하고 진행됐다"
                assert not probe.wrote_ledger(), "PI 잠금 이전에 원장 INSERT가 나갔다"
            finally:
                tx.rollback()
                holder.close()
            return future.result(timeout=20)
    finally:
        event.remove(engine, "before_cursor_execute", probe)
        if holder.closed is False:
            tx.rollback()
            holder.close()


def test_the_pi_row_lock_serializes_a_receipt_behind_a_holder() -> None:
    """결정적 직렬화 증거 — 다른 트랜잭션이 PI 행을 잡고 있으면 입금은 대기하고(원장 INSERT도 잠금 뒤) 놓은 뒤에 완료된다(PI 잠금을 빼면 INSERT가 먼저 나가 이 테스트가 깨진다)"""
    who = actor()
    pi = advance_pi()
    status, body = _run_behind_a_pi_lock_holder(pi, lambda: _receipt(who, pi, "100.00"))
    assert status == 201 and body["summary"]["net_received_amount"] == 10_000
    assert len(ledger(pi)) == 1


def test_a_convergence_failure_rolls_back_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """입금 수렴 중 실패 → 완전 롤백 — 원장 행·PI 상태·상태 이력·audit·outbox·멱등 기록이 전부 남지 않고, 같은 키 재시도는 새로 처리된다"""
    who = actor()
    pi = advance_pi()
    key = unique("fail")
    before_log = len(status_log(pi))

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("수렴 실패(주입)")

    monkeypatch.setattr(payment_flow, "converge_payment_status", boom)
    with pytest.raises(RuntimeError):
        _receipt(who, pi, "100.00", key=key)
    assert ledger(pi) == [] and pi_status(pi) == "ISSUED" and len(status_log(pi)) == before_log
    assert scalar("SELECT count(*) FROM audit_log WHERE action LIKE 'payments.%'") == 0
    assert scalar("SELECT count(*) FROM events WHERE event_type LIKE 'payments.%'") == 0
    assert scalar("SELECT count(*) FROM idempotency_keys WHERE idempotency_key = :k", k=key) == 0
    monkeypatch.undo()
    status, _body = _receipt(
        who, pi, "100.00", key=key
    )  # 같은 키 재시도 — 실패가 키를 태우지 않았다
    assert status == 201 and len(ledger(pi)) == 1


def test_a_reversal_convergence_failure_rolls_back_the_negative_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """역기록의 수렴 실패 → 반대 부호 행·상태 변경·경고 이벤트가 남지 않는다(원 입금만 남아 순입금 불변)"""
    who = actor()
    pi = advance_pi()
    _s, paid = _receipt(who, pi, "100.00")

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("수렴 실패(주입)")

    monkeypatch.setattr(payment_flow, "converge_payment_status", boom)
    with pytest.raises(RuntimeError):
        payment_flow.reverse_payment(
            actor=who,
            idempotency_key=unique("rf"),
            payment_id=paid["payment"]["id"],
            reason="정정함",
        )
    assert [r["kind"] for r in ledger(pi)] == ["RECEIPT"] and pi_status(pi) == "PARTIALLY_PAID"
    assert scalar("SELECT count(*) FROM events WHERE event_type = 'payments.payment.reversed'") == 0


def test_reversal_versus_a_confirm_style_pi_lock_holder_is_serialized() -> None:
    """확정 통로가 PI를 잡는 상황의 대역 — PI 행을 잡은 트랜잭션이 있는 동안 역기록은 대기하고(원장 INSERT도 잠금 뒤), 놓은 뒤 정상 완료되어 직렬 순서 중 하나가 된다"""
    who = actor()
    pi = advance_pi()
    _s, paid = _receipt(who, pi, "100.00")
    status, body = _run_behind_a_pi_lock_holder(
        pi,
        lambda: payment_flow.reverse_payment(
            actor=who,
            idempotency_key=unique("rv"),
            payment_id=paid["payment"]["id"],
            reason="확정과 경합",
        ),
    )
    assert status == 201 and body["summary"]["net_received_amount"] == 0


# ── 잠금 순서(LOCK_ORDER) 계측 ─────────────────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "quotations": "quotations",
    "proforma_invoices": "proforma_invoices",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE_LOCK = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _lock_sequence(work: Any) -> list[str]:
    order: list[str] = []

    def listener(*args: object) -> None:
        statement = str(args[2])
        table: str | None = None
        if _LOCK_CLAUSE.search(statement):
            found = _FROM.search(statement)
            table = found.group(1) if found else None
        else:
            found = _WRITE_LOCK.match(statement)
            if found and found.group(1) == "idempotency_keys":
                table = found.group(1)
        name = _LOCK_TABLES.get(table or "")
        if name and name not in order:
            order.append(name)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return order


def test_receipt_and_reversal_take_locks_in_the_documented_order() -> None:
    """입금·역기록의 행 잠금은 멱등 → QT → PI(LOCK_ORDER) 순서다 — 거꾸로 잡는 경로가 없어 교착이 없다(공회전 방지로 세 잠금이 모두 관측됨을 확인)"""
    who = actor()
    pi = advance_pi()
    holder: dict[str, Any] = {}

    def pay() -> None:
        _s, holder["paid"] = _receipt(who, pi, "100.00")

    seen = _lock_sequence(pay)
    assert seen[:3] == ["idempotency_keys", "quotations", "proforma_invoices"], seen
    indexes = [LOCK_ORDER.index(n) for n in seen]
    assert indexes == sorted(indexes), seen

    def back() -> None:
        payment_flow.reverse_payment(
            actor=who,
            idempotency_key=unique("lo"),
            payment_id=holder["paid"]["payment"]["id"],
            reason="순서 계측",
        )

    seen_reversal = _lock_sequence(back)
    assert seen_reversal[:3] == ["idempotency_keys", "quotations", "proforma_invoices"], (
        seen_reversal
    )
    assert [LOCK_ORDER.index(n) for n in seen_reversal] == sorted(
        LOCK_ORDER.index(n) for n in seen_reversal
    )
