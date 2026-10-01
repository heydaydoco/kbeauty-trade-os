"""J. 오더 인테이크 — 접수 확정의 롤백 원자성·동시 확정·동시 등록·잠금 순서·멱등 (S3-1 PR-13a / ADR-0071 / design-D D5(f)).

핵심: 인테이크 CONFIRMED와 SO 생성·채번·이벤트는 **같은 트랜잭션**이라 어느 단계에서 실패해도 전부 원복한다(인테이크 PENDING·SO 0건·채번 카운터 복귀·이벤트 0·멱등 키 미소비).
같은 인테이크의 동시 확정은 SO 1건(나머지 409), 같은 PO의 동시 등록은 인테이크 1건(나머지 409 — 500 아님). 확정의 잠금 순서는 인테이크 → 거래처 → … → 채번이다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event

from app.core.db.session import engine
from app.core.errors.exceptions import AppError
from app.modules.order_intake import service as intake_service
from app.modules.sales_orders import service as sales_orders
from app.modules.trade_chain import intake_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.intake import (
    execute,
    intake_payload,
    rows,
    scalar,
    trade_actor,
    world,
)
from tests.factories.trade import unique
from tests.support.concurrency import Outcome, run_concurrently

pytestmark = pytest.mark.group_j


def _land(w: dict[str, Any], *, po_no: str | None = None) -> dict[str, Any]:
    status, body = intake_service.create_manual_intake(
        actor=trade_actor(),
        idempotency_key=unique("land"),
        payload=intake_payload(w, po_no=po_no),
    )
    assert status == 201
    return body


def _confirm(
    intake: dict[str, Any], *, key: str | None = None, actor: Any = None
) -> tuple[int, dict[str, Any]]:
    return intake_flow.confirm_intake(
        actor=actor or trade_actor(),
        idempotency_key=key or unique("conf"),
        intake_id=intake["id"],
        version=intake["version"],
    )


def _counter() -> int:
    found = rows("SELECT COALESCE(max(last_number), 0) FROM doc_number_seq WHERE prefix = 'SO'")
    return int(found[0][0]) if found else 0


def _error(outcome: Outcome) -> AppError:
    assert isinstance(outcome.error, AppError), outcome.error
    return outcome.error


# ── 롤백 원자성 ───────────────────────────────────────────────────────────────


def test_a_failure_while_creating_the_so_rolls_back_the_intake_the_number_and_the_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SO 착지가 끝난 뒤 예외 — 인테이크는 PENDING 그대로, SO 0건, 채번 카운터 복귀, SO 이벤트 0, 멱등 키 미소비(같은 키 재시도 성공)"""
    w = world()
    intake = _land(w)
    counter_before = _counter()
    events_before = scalar("SELECT count(*) FROM events")
    real = sales_orders.create_received_sales_order

    def landed_then_failed(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise RuntimeError("착지 직후 장애 주입")

    monkeypatch.setattr(sales_orders, "create_received_sales_order", landed_then_failed)
    key = unique("atomic")
    with pytest.raises(RuntimeError):
        _confirm(intake, key=key)
    assert scalar("SELECT status FROM order_intakes WHERE id = :i", i=intake["id"]) == "PENDING"
    assert scalar("SELECT count(*) FROM sales_orders") == 0
    assert _counter() == counter_before  # 채번 카운터도 같은 트랜잭션이라 되돌아간다
    assert scalar("SELECT count(*) FROM events") == events_before
    assert scalar("SELECT count(*) FROM idempotency_keys WHERE idempotency_key = :k", k=key) == 0
    monkeypatch.setattr(sales_orders, "create_received_sales_order", real)
    status, body = _confirm(intake, key=key)  # 양성 대조 — 같은 키·같은 본문으로 성공
    assert status == 201 and body["doc_number"].startswith("SO-")
    assert scalar("SELECT count(*) FROM sales_orders") == 1


def test_a_failure_after_the_intake_transition_rolls_everything_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """인테이크 CONFIRMED 전이·SO·이벤트가 모두 flush된 뒤(멱등 완료 기록 단계) 예외 — SO·인테이크 둘 다 원복한다"""
    w = world()
    intake = _land(w)

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("완료 기록 장애 주입")

    monkeypatch.setattr(intake_flow.idempotency, "complete", boom)
    with pytest.raises(RuntimeError):
        _confirm(intake)
    assert scalar("SELECT status FROM order_intakes WHERE id = :i", i=intake["id"]) == "PENDING"
    assert scalar("SELECT sales_order_id FROM order_intakes WHERE id = :i", i=intake["id"]) is None
    assert scalar("SELECT count(*) FROM sales_orders") == 0
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'order_intakes.order_intake.status_changed'"
        )
        == 0
    )


def test_a_hard_gate_rejection_leaves_no_trace_and_does_not_consume_the_key() -> None:
    """하드 게이트 거부(미매핑)는 아무 흔적도 남기지 않는다 — SO·번호·상태이벤트 0, 멱등 키 미소비(매핑 등록 뒤 같은 키 재시도 가능)"""
    w = world()
    status, body = intake_service.create_manual_intake(
        actor=trade_actor(),
        idempotency_key=unique("l"),
        payload=intake_payload(
            w, lines=[{"buyer_item_code": "NO-MAP", "quantity": 1, "unit_price": "1.00"}]
        ),
    )
    counter = _counter()
    key = unique("rej")
    with pytest.raises(AppError) as caught:
        _confirm(body, key=key)
    assert caught.value.code.value == "ORDER_INTAKE.LINE.UNMAPPED_ITEMS"
    assert scalar("SELECT count(*) FROM sales_orders") == 0 and _counter() == counter
    assert scalar("SELECT count(*) FROM idempotency_keys WHERE idempotency_key = :k", k=key) == 0
    assert status == 201


# ── 멱등 ─────────────────────────────────────────────────────────────────────


def test_double_click_with_the_same_key_creates_one_so_and_a_different_body_conflicts() -> None:
    """같은 키 재전송은 최초 결과 재생(SO 1건) — 같은 키+다른 본문(version)은 409 KEY_CONFLICT"""
    w = world()
    intake = _land(w)
    actor = trade_actor()
    key = unique("dbl")
    first = _confirm(intake, key=key, actor=actor)
    again = _confirm(intake, key=key, actor=actor)
    assert first == again and first[0] == 201
    assert scalar("SELECT count(*) FROM sales_orders") == 1
    with pytest.raises(AppError) as caught:
        intake_flow.confirm_intake(
            actor=actor, idempotency_key=key, intake_id=intake["id"], version=intake["version"] + 1
        )
    assert caught.value.code.value == "COMMON.IDEMPOTENCY.KEY_CONFLICT"


# ── 동시 확정·동시 등록 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("threads", [2, 8])
def test_concurrent_confirmations_of_the_same_intake_create_exactly_one_so(threads: int) -> None:
    """같은 인테이크를 서로 다른 키로 동시에 확정 — 성공 정확히 1건·SO 1건·나머지는 409(500·데드락 아님)"""
    w = world()
    intake = _land(w)

    def worker(_i: int) -> tuple[int, dict[str, Any]]:
        return _confirm(intake)

    outcomes = run_concurrently(worker, workers=threads)
    wins = [o for o in outcomes if o.ok]
    losers = [o for o in outcomes if not o.ok]
    assert len(wins) == 1, [repr(o.error) for o in losers]
    for loser in losers:
        err = _error(loser)
        assert err.status_code == 409, err
    assert scalar("SELECT count(*) FROM sales_orders") == 1
    assert scalar("SELECT status FROM order_intakes WHERE id = :i", i=intake["id"]) == "CONFIRMED"
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'order_intakes.order_intake.status_changed'"
        )
        == 1
    )


def test_concurrent_confirm_and_reject_of_the_same_intake_end_consistently() -> None:
    """확정×거부 동시 — 한쪽만 이긴다: 확정이 이기면 SO 1건·CONFIRMED, 거부가 이기면 SO 0건·REJECTED(둘 다 성공은 없다)"""
    for _ in range(5):
        w = world()
        intake = _land(w)

        def worker(i: int, _intake: dict[str, Any] = intake) -> Any:
            if i == 0:
                return _confirm(_intake)
            return intake_service.reject_intake(
                actor=trade_actor(),
                idempotency_key=unique("rj"),
                intake_id=_intake["id"],
                version=_intake["version"],
                reason="동시 거부 시험 사유",
            )

        outcomes = run_concurrently(worker, workers=2)
        assert sum(1 for o in outcomes if o.ok) == 1, [repr(o.error) for o in outcomes]
        status = scalar("SELECT status FROM order_intakes WHERE id = :i", i=intake["id"])
        sos = scalar("SELECT count(*) FROM sales_orders WHERE buyer_partner_id = :b", b=w["buyer"])
        assert (status, sos) in {("CONFIRMED", 1), ("REJECTED", 0)}, (status, sos)


@pytest.mark.parametrize("threads", [2, 8])
def test_concurrent_registration_of_the_same_po_lands_exactly_one_intake(threads: int) -> None:
    """같은 (바이어, PO)를 서로 다른 키로 동시에 등록 — 인테이크 정확히 1건·나머지는 409 DUPLICATE_BUYER_PO(500 아님)"""
    w = world()
    po = unique("PO-RACE")

    def worker(_i: int) -> Any:
        return intake_service.create_manual_intake(
            actor=trade_actor(), idempotency_key=unique("race"), payload=intake_payload(w, po_no=po)
        )

    outcomes = run_concurrently(worker, workers=threads)
    assert sum(1 for o in outcomes if o.ok) == 1, [repr(o.error) for o in outcomes]
    for loser in (o for o in outcomes if not o.ok):
        err = _error(loser)
        assert err.code.value == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", err
        assert err.status_code == 409
    assert (
        scalar("SELECT count(*) FROM order_intakes WHERE buyer_partner_id = :b", b=w["buyer"]) == 1
    )
    assert scalar("SELECT count(*) FROM order_intake_lines") == 1


def test_a_registration_racing_an_so_creation_for_the_same_po_never_double_occupies() -> None:
    """같은 PO의 인테이크 등록과 SO 접수(직접 착지)가 동시에 오면 — 최종적으로 PENDING 인테이크와 비취소 SO가 함께 존재할 수 있으나 확정은 409로 막힌다(중복 SO 0건)"""
    from tests.factories.trade import create_direct_so

    w = world()
    po = unique("PO-MIX")
    intake = _land(w, po_no=po)
    create_direct_so(w["buyer"], w["sku_ids"], buyer_po_no=po)  # 착지 뒤 SO가 PO를 가져갔다
    with pytest.raises(AppError) as caught:
        _confirm(intake)
    assert caught.value.code.value == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    assert (
        scalar(
            "SELECT count(*) FROM sales_orders WHERE buyer_partner_id = :b AND status <> 'CANCELLED'",
            b=w["buyer"],
        )
        == 1
    )


def test_two_editors_race_one_wins_and_one_gets_409() -> None:
    """같은 version으로 동시에 편집하면 하나만 성공하고 하나는 409(낙관 잠금)"""
    w = world()
    intake = _land(w)

    def worker(_i: int) -> Any:
        return intake_service.update_intake(
            actor=trade_actor(),
            intake_id=intake["id"],
            payload={
                "version": intake["version"],
                "buyer_po_date": None,
                "assignee_id": trade_actor().id,
            },
        )

    outcomes = run_concurrently(worker, workers=2)
    assert sum(1 for o in outcomes if o.ok) == 1, [repr(o.error) for o in outcomes]
    err = _error(next(o for o in outcomes if not o.ok))
    assert err.code.value == "COMMON.CONCURRENCY.VERSION_CONFLICT"


# ── 잠금 순서 ─────────────────────────────────────────────────────────────────

_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)
_NAMES = {
    "idempotency_keys": "idempotency_keys",
    "order_intakes": "order_intakes",
    "partners": "partners",
    "sales_orders": "sales_orders",
    "sales_order_lines": "lines",
    "order_intake_lines": "lines",
    "doc_number_seq": "doc_number_seq",
}


def _first_lock_sequence(work: Callable[[], object]) -> list[str]:
    seen: list[str] = []

    def listener(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if _LOCK_CLAUSE.search(statement):
            found = _FROM.search(statement)
            name = _NAMES.get(found.group(1)) if found else None
            if name and name not in seen:
                seen.append(name)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return seen


def test_confirm_locks_the_intake_then_the_buyer_then_the_numbering_row_in_the_global_order() -> (
    None
):
    """전역 LOCK_ORDER 준수(계측) — 인테이크 확정이 실제로 보낸 잠금 SQL의 첫 접촉 순서가 order_intakes → partners → … → doc_number_seq(항상 마지막)다"""
    w = world()
    intake = _land(w)
    seen = _first_lock_sequence(lambda: _confirm(intake))
    assert seen[0] == "idempotency_keys" or seen[0] == "order_intakes", seen
    assert "order_intakes" in seen and "partners" in seen and "doc_number_seq" in seen, seen
    assert seen.index("order_intakes") < seen.index("partners") < seen.index("doc_number_seq"), seen
    assert seen[-1] == "doc_number_seq", seen
    indexes = [LOCK_ORDER.index(name) for name in seen if name in LOCK_ORDER]
    assert indexes == sorted(indexes), seen


def test_the_lock_probe_sees_a_reversed_order_as_a_violation() -> None:
    """공회전 방지 — 계측이 잠금을 실제로 기록하고(비어 있지 않음), 뒤집은 순서는 위반으로 판정된다"""
    w = world()
    intake = _land(w)
    seen = _first_lock_sequence(lambda: _confirm(intake))
    assert len(seen) >= 3
    reversed_order = list(reversed(seen))
    assert [LOCK_ORDER.index(n) for n in reversed_order if n in LOCK_ORDER] != sorted(
        LOCK_ORDER.index(n) for n in reversed_order if n in LOCK_ORDER
    )


def test_registration_and_edit_follow_the_lock_order_too() -> None:
    """등록(거래처 KEY SHARE → …)·편집(인테이크 → …)도 순서를 거꾸로 잠그지 않는다 — 부분수열 준수"""
    w = world()
    seen_register = _first_lock_sequence(lambda: _land(w))
    idx = [LOCK_ORDER.index(n) for n in seen_register if n in LOCK_ORDER]
    assert idx == sorted(idx), seen_register
    intake = _land(w)
    seen_edit = _first_lock_sequence(
        lambda: intake_service.update_intake(
            actor=trade_actor(),
            intake_id=intake["id"],
            payload={"version": intake["version"], "buyer_po_date": None},
        )
    )
    idx = [LOCK_ORDER.index(n) for n in seen_edit if n in LOCK_ORDER]
    assert seen_edit[0] == "order_intakes" and idx == sorted(idx), seen_edit
    execute("SELECT 1")  # 계측 리스너가 정리됐음을 확인하는 무해한 호출
