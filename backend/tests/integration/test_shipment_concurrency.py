"""J·H. 선적 동시성 — 실제 동시 부분선적·더블클릭·20명 경합·생성 vs SO 취소·마지막 선적 취소 vs 새 선적·잠금 순서·잠금 대기 초과
(S3-2 PR-3a / GC-F4 / design-C C2·C3·C14 J-01~J-07·J-15 / ADR-0078).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다.
  결과 불변식: 잔량 초과 0 · 500·교착(40P01) 0 · 확정 SO의 IN_SHIPMENT ⇔ 살아 있는 선적 ≥ 1(거짓 상태 0).
★ SO `FOR UPDATE` 선점(SHARE→UPDATE 승격 금지 — ADR-0078)이 없으면 GC-F4가 교착·초과로 깨진다(변이 점검 대상).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.core.db.session import engine, owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.trade_chain import lifecycle, shipment_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.approvals import make_user
from tests.factories.shipments import confirmed_so, create_body, scalar, so_status, so_version
from tests.factories.trade import create_supplier, unique
from tests.support.concurrency import Outcome, run_concurrently

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]


def _create(
    actor: AuthenticatedUser, so_id: int, lines: list[tuple[int, int]], key: str | None = None
) -> tuple[int, dict[str, Any]]:
    return shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=key or unique("sh"),
        so_id=so_id,
        payload=create_body(lines),
    )


def _cancel_shipment(actor: AuthenticatedUser, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    version = int(scalar("SELECT version FROM shipments WHERE id = :i", i=body["id"]))
    return shipment_flow.transition_shipment(
        actor=actor,
        idempotency_key=unique("shx"),
        shipment_id=body["id"],
        to="CANCELLED",
        version=version,
        reason="동시성",
    )


def _codes(outcomes: list[Outcome]) -> list[str]:
    return sorted(
        o.error.code if isinstance(o.error, AppError) else type(o.error).__name__
        for o in outcomes
        if not o.ok
    )


def _no_db_errors(outcomes: list[Outcome]) -> None:
    for outcome in outcomes:
        assert outcome.ok or isinstance(outcome.error, AppError), repr(outcome.error)


def _open(so_line_id: int) -> int:
    return int(
        scalar(
            "SELECT sol.quantity - COALESCE((SELECT SUM(sl.quantity) FROM shipment_lines sl"
            " JOIN shipments s ON s.id = sl.shipment_id WHERE sl.so_line_id = sol.id"
            " AND sl.deleted_at IS NULL AND s.deleted_at IS NULL AND s.status NOT IN ('CANCELLED','EXPIRED')), 0)"
            " FROM sales_order_lines sol WHERE sol.id = :i",
            i=so_line_id,
        )
    )


def _live_shipments(so_id: int) -> int:
    return int(
        scalar(
            "SELECT count(*) FROM shipments WHERE so_id = :s AND deleted_at IS NULL"
            " AND status NOT IN ('CANCELLED','EXPIRED')",
            s=so_id,
        )
    )


def _assert_convergence_invariant(so_id: int) -> None:
    """확정 SO의 IN_SHIPMENT ⇔ 살아 있는 선적 ≥ 1 — 거짓 상태 0."""
    status = so_status(so_id)
    if status in ("CONFIRMED", "IN_SHIPMENT"):
        assert (status == "IN_SHIPMENT") == (_live_shipments(so_id) >= 1), (status, so_id)


@pytest.mark.golden
def test_gc_f4_concurrent_partial_shipments_7_plus_7_on_10() -> None:
    """GC-F4 — SO 라인 10에 다른 키로 7+7 동시 선적 → 정확히 1 성공·1 × 409 EXCEEDS_OPEN, 잔량 3, 500·40P01 0, SO 선적중"""
    so = confirmed_so((10,))
    line = so["line_ids"][0]
    actors = [make_user(RoleCode.TRADE) for _ in range(2)]
    outcomes = run_concurrently(lambda i: _create(actors[i], so["id"], [(line, 7)]), workers=2)
    _no_db_errors(outcomes)
    assert sum(o.ok for o in outcomes) == 1
    assert _codes(outcomes) == ["TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"]
    assert _open(line) == 3
    assert so_status(so["id"]) == "IN_SHIPMENT" and _live_shipments(so["id"]) == 1
    assert (
        scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :s AND to_status = 'IN_SHIPMENT'",
            s=so["id"],
        )
        == 1
    )


def test_double_click_from_six_threads_creates_one_shipment() -> None:
    """J-01 — 같은 키·같은 본문 6스레드 동시 → 선적 1건·번호 1개·created 이벤트 1건·SO 수렴 이력 1행, 전 스레드 같은 응답"""
    so = confirmed_so((10,))
    actor = make_user(RoleCode.TRADE)
    key = unique("dbl")
    outcomes = run_concurrently(
        lambda _i: _create(actor, so["id"], [(so["line_ids"][0], 3)], key=key), workers=6
    )
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    bodies = [o.value[1] for o in outcomes]
    assert all(body == bodies[0] for body in bodies)
    assert scalar("SELECT count(*) FROM shipments WHERE so_id = :s", s=so["id"]) == 1
    assert (
        scalar("SELECT count(*) FROM events WHERE event_type = 'shipments.shipment.created'") == 1
    )
    assert (
        scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :s AND to_status = 'IN_SHIPMENT'",
            s=so["id"],
        )
        == 1
    )
    assert _open(so["line_ids"][0]) == 7


def test_twenty_users_racing_for_fifteen_units() -> None:
    """J-03 — SO 라인 15에 20명이 1개씩 동시 선적 → 성공 15·EXCEEDS_OPEN 5·잔량 0·교착 0·번호 15개 중복 0"""
    so = confirmed_so((15,))
    actors = [make_user(RoleCode.TRADE) for _ in range(20)]
    outcomes = run_concurrently(
        lambda i: _create(actors[i], so["id"], [(so["line_ids"][0], 1)]), workers=20, timeout=60
    )
    _no_db_errors(outcomes)
    assert sum(o.ok for o in outcomes) == 15
    assert _codes(outcomes) == ["TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"] * 5
    assert _open(so["line_ids"][0]) == 0
    numbers = [o.value[1]["doc_number"] for o in outcomes if o.ok]
    assert len(set(numbers)) == 15
    _assert_convergence_invariant(so["id"])


def _race_create_against_so_cancel() -> None:
    so = confirmed_so((10,))
    trade, canceller = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    version = so_version(so["id"])
    line = so["line_ids"][0]

    def work(i: int) -> Any:
        if i == 0:
            return _create(trade, so["id"], [(line, 2)])
        return lifecycle.transition_sales_order(
            actor=canceller,
            idempotency_key=unique("soc"),
            so_id=so["id"],
            to="CANCELLED",
            version=version,
            reason="동시 취소",
        )

    outcomes = run_concurrently(work, workers=2)
    _no_db_errors(outcomes)
    assert sum(o.ok for o in outcomes) == 1, [repr(o.error) for o in outcomes]
    create, cancel = outcomes
    if create.ok:
        assert isinstance(cancel.error, AppError)
        assert cancel.error.code in (
            "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE",
            "COMMON.CONCURRENCY.VERSION_CONFLICT",
        )
        assert so_status(so["id"]) == "IN_SHIPMENT"
    else:
        assert isinstance(create.error, AppError)
        assert create.error.code == "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
        assert so_status(so["id"]) == "CANCELLED" and _live_shipments(so["id"]) == 0


def test_shipment_creation_racing_an_so_cancel_never_both_succeed() -> None:
    """J-04 — 선적 생성 vs 같은 SO 취소 동시(6회) → 둘 중 하나만 성공: 선적 먼저면 취소 409(SUCCESSOR_ALIVE·VERSION_CONFLICT), 취소 먼저면 선적 409 NOT_CONSUMABLE"""
    for _ in range(6):
        _race_create_against_so_cancel()


def _race_last_cancel_against_new_shipment() -> None:
    so = confirmed_so((10,))
    actor_a, actor_b = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    line = so["line_ids"][0]
    _, first = _create(actor_a, so["id"], [(line, 3)])

    def work(i: int) -> Any:
        if i == 0:
            return _cancel_shipment(actor_a, first)
        return _create(actor_b, so["id"], [(line, 3)])

    outcomes = run_concurrently(work, workers=2)
    _no_db_errors(outcomes)
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    assert _live_shipments(so["id"]) == 1
    _assert_convergence_invariant(so["id"])
    assert so_status(so["id"]) == "IN_SHIPMENT"


def test_last_shipment_cancel_racing_a_new_shipment_keeps_the_so_state_true() -> None:
    """J-05 — 마지막 살아 있는 선적 취소 vs 같은 SO 새 선적 생성 동시(6회) → 둘 다 성공해도 최종 SO 상태 = 살아 있는 선적 유무(거짓 상태 0)"""
    for _ in range(6):
        _race_last_cancel_against_new_shipment()


def test_cancelling_two_shipments_at_once_returns_the_so_to_confirmed_exactly_once() -> None:
    """J-05 보강 — 같은 SO의 선적 2건을 동시에 취소 → 둘 다 성공·SO 확정 복귀 이력 정확히 1행·최종 CONFIRMED"""
    so = confirmed_so((10,))
    actors = [make_user(RoleCode.TRADE) for _ in range(2)]
    bodies = [_create(actors[i], so["id"], [(so["line_ids"][0], 2)])[1] for i in range(2)]
    outcomes = run_concurrently(lambda i: _cancel_shipment(actors[i], bodies[i]), workers=2)
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    assert so_status(so["id"]) == "CONFIRMED"
    assert (
        scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :s"
            " AND from_status = 'IN_SHIPMENT' AND to_status = 'CONFIRMED'",
            s=so["id"],
        )
        == 1
    )


# ── 잠금 대기 초과(55P03) ──────────────────────────────────────────────────────


def test_a_held_so_lock_surfaces_as_lock_not_available_not_a_hang() -> None:
    """J-15 — 다른 TX가 SO 행을 잡은 동안 선적 생성은 무한 대기가 아니라 55P03(→ API 409 LOCK_BUSY)이고 부작용 0"""
    so = confirmed_so((10,))
    actor = make_user(RoleCode.TRADE)
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM sales_orders WHERE id = :i FOR UPDATE"), {"i": so["id"]})
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            shipment_flow._plan(
                uow.session, actor, so["id"], create_body([(so["line_ids"][0], 2)]), lock=True
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        tx.rollback()
        holder.close()
    assert scalar("SELECT count(*) FROM shipments") == 0 and so_status(so["id"]) == "CONFIRMED"


# ── 잠금 순서(LOCK_ORDER) 계측 — J-07 ─────────────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "sales_orders": "sales_orders",
    "purchase_orders": "purchase_orders",
    "shipments": "shipments",
    "shipment_parties": "shipment_children",
    "sales_order_lines": "lines",
    "shipment_lines": "lines",
    "doc_number_seq": "doc_number_seq",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE_LOCK = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _first_lock_sequence(work: Callable[[], object]) -> list[str]:
    """work가 실행하는 SQL에서 LOCK_ORDER 대상의 **첫 접촉** 순서(행 잠금 절 또는 멱등·채번 쓰기)."""
    order: list[str] = []

    def listener(*args: object) -> None:
        statement = str(args[2])
        table: str | None = None
        if _LOCK_CLAUSE.search(statement):
            found = _FROM.search(statement)
            table = found.group(1) if found else None
        else:
            found = _WRITE_LOCK.match(statement)
            if found and found.group(1) in ("idempotency_keys", "doc_number_seq"):
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


def _assert_follows_lock_order(sequence: list[str]) -> None:
    indexes = [LOCK_ORDER.index(name) for name in sequence]
    assert indexes == sorted(indexes), f"잠금 순서 위반: {sequence} (기준 {LOCK_ORDER})"


def test_every_shipment_operation_takes_locks_in_the_documented_order() -> None:
    """J-07 — 생성(멱등→거래처→SO→라인→채번)·라인 추가/수정/삭제(멱등→SO→선적→라인)·출고지시·취소(멱등→SO→선적)·당사자 추가(멱등→거래처→선적)·
    당사자 제외(선적→하위 행)가 개정 LOCK_ORDER(…PO → shipments → shipment_children → approvals → lines → seq)를 지킨다"""
    so = confirmed_so((10, 10))
    actor = make_user(RoleCode.TRADE)
    a, b = so["line_ids"]
    holder: dict[str, Any] = {}

    def create() -> None:
        holder["s"] = _create(actor, so["id"], [(a, 2)])[1]

    seen = _first_lock_sequence(create)
    assert seen == ["idempotency_keys", "partners", "sales_orders", "lines", "doc_number_seq"], seen
    _assert_follows_lock_order(seen)
    shipment = holder["s"]

    def version() -> int:
        return int(scalar("SELECT version FROM shipments WHERE id = :i", i=shipment["id"]))

    def add_line() -> None:
        holder["s"] = shipment_flow.add_line(
            actor=actor,
            idempotency_key=unique("la"),
            shipment_id=shipment["id"],
            payload={"version": version(), "source_line_id": b, "quantity": 1},
        )[1]

    seen = _first_lock_sequence(add_line)
    assert seen == ["idempotency_keys", "sales_orders", "shipments", "lines"], seen
    _assert_follows_lock_order(seen)
    line_b = next(ln for ln in holder["s"]["lines"] if ln["so_line_id"] == b)

    edits: tuple[Callable[[], object], ...] = (
        lambda: shipment_flow.update_line(
            actor=actor,
            shipment_id=shipment["id"],
            line_id=line_b["id"],
            payload={"version": version(), "quantity": 2},
        ),
        lambda: shipment_flow.remove_line(
            actor=actor, shipment_id=shipment["id"], line_id=line_b["id"], version=version()
        ),
    )
    for work in edits:
        seen = _first_lock_sequence(work)
        assert seen[:2] == ["sales_orders", "shipments"], seen
        _assert_follows_lock_order(seen)

    forwarder = create_supplier(types=("FORWARDER",))

    def add_party() -> None:
        holder["s"] = shipment_flow.add_party(
            actor=actor,
            idempotency_key=unique("pa"),
            shipment_id=shipment["id"],
            payload={"role": "FORWARDER", "partner_id": forwarder},
        )[1]

    seen = _first_lock_sequence(add_party)
    assert seen == ["idempotency_keys", "partners", "shipments"], seen
    _assert_follows_lock_order(seen)
    party = next(p for p in holder["s"]["parties"] if p["role"] == "FORWARDER")

    seen = _first_lock_sequence(
        lambda: shipment_flow.remove_party(
            actor=actor, shipment_id=shipment["id"], party_id=party["id"], version=party["version"]
        )
    )
    assert seen == ["shipments", "shipment_children"], seen
    _assert_follows_lock_order(seen)

    transitions: tuple[Callable[[], object], ...] = (
        lambda: shipment_flow.release_shipment_order(
            actor=actor, idempotency_key=unique("rl"), shipment_id=shipment["id"], version=version()
        ),
        lambda: _cancel_shipment(actor, shipment),
    )
    for work in transitions:
        seen = _first_lock_sequence(work)
        assert seen == ["idempotency_keys", "sales_orders", "shipments"], seen
        _assert_follows_lock_order(seen)
    assert so_status(so["id"]) == "CONFIRMED"
