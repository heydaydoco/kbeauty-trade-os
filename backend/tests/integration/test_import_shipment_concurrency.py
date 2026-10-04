"""J. 수입선적 동시성 — 동시 수입 2건·다른 라인 동시·더블클릭·생성 vs PO 취소·잠금 순서·PO 공유 잠금·잠금 대기 초과
(S3-2 PR-5a / design-C J-06·J-07·J-15 / ADR-0077·0078 R-08).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다.
  결과 불변식: 배정 가능량 초과 0 · 500·교착(40P01) 0 · PO 잔량(FULFILL)·상태 불변 · 취소된 PO 아래 살아 있는 수입선적 0.
★ 수입 생성(T2)의 PO 잠금은 **FOR SHARE**다(PO 상태를 바꾸지 않아 승격이 없다 — R-08). 같은 PO의 서로 다른 라인 동시 생성은 서로 막지 않고,
  같은 라인은 PO 라인 `FOR UPDATE`가 직렬화한다(두 번째는 첫 번째 커밋 뒤 배정 가능량을 본다).
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
from app.modules.trade_docs.quantities import ASSIGNABLE_KINDS, open_quantity
from tests.factories.approvals import make_user
from tests.factories.shipments import import_body, scalar
from tests.factories.trade import (
    create_po_via_api,
    create_purchase_priced_sku,
    create_supplier,
    logged_in,
    unique,
)
from tests.support.concurrency import Outcome, run_concurrently

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]


def _issued_po(quantities: tuple[int, ...]) -> dict[str, Any]:
    with logged_in(RoleCode.TRADE) as trade:
        return create_po_via_api(
            trade,
            create_supplier(),
            [],
            lines=[{"sku_id": create_purchase_priced_sku(), "quantity": qty} for qty in quantities],
        )


def _create(
    actor: AuthenticatedUser, po_id: int, lines: list[tuple[int, int]], key: str | None = None
) -> tuple[int, dict[str, Any]]:
    return shipment_flow.create_shipment_from_purchase_order(
        actor=actor, idempotency_key=key or unique("im"), po_id=po_id, payload=import_body(lines)
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


def _open(line_id: int) -> tuple[int, int]:
    """(PO 잔량[FULFILL], 배정 가능량[IN_TRANSIT])."""
    with unit_of_work() as uow:
        return (
            open_quantity(uow.session, "PO_LINE", [line_id])[line_id].open,
            open_quantity(uow.session, "PO_LINE", [line_id], kinds=ASSIGNABLE_KINDS)[line_id].open,
        )


def _po_state(po_id: int) -> tuple[str, int]:
    with owner_engine.connect() as connection:
        status, version = connection.execute(
            text("SELECT status, version FROM purchase_orders WHERE id = :i"), {"i": po_id}
        ).one()
    return str(status), int(version)


@pytest.mark.golden
def test_j06_two_concurrent_import_shipments_never_exceed_the_assignable_quantity() -> None:
    """J-06(GC-A15 동시판) — PO 라인 100에 다른 키로 60+60 동시 수입선적 → 정확히 1 성공·1 × 409 EXCEEDS_ASSIGNABLE, 배정 가능량 40,
    PO 잔량 100 그대로·PO 상태·version 불변, 500·40P01 0"""
    po = _issued_po((100,))
    line = po["lines"][0]["id"]
    before = _po_state(po["id"])
    actors = [make_user(RoleCode.TRADE) for _ in range(2)]
    outcomes = run_concurrently(lambda i: _create(actors[i], po["id"], [(line, 60)]), workers=2)
    _no_db_errors(outcomes)
    assert sum(o.ok for o in outcomes) == 1
    assert _codes(outcomes) == ["SHIPMENTS.QUANTITY.EXCEEDS_ASSIGNABLE"]
    assert _open(line) == (100, 40)
    assert _po_state(po["id"]) == before


def test_concurrent_import_shipments_on_different_lines_of_one_po_both_succeed() -> None:
    """R-08 — 같은 PO의 서로 다른 라인 동시 수입선적 6건 → 전부 성공(PO FOR SHARE는 서로 막지 않고 승격이 없어 교착 0), 번호 중복 0"""
    po = _issued_po((5, 5, 5, 5, 5, 5))
    lines = [line["id"] for line in po["lines"]]
    actors = [make_user(RoleCode.TRADE) for _ in range(6)]
    outcomes = run_concurrently(
        lambda i: _create(actors[i], po["id"], [(lines[i], 5)]), workers=6, timeout=60
    )
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    assert len({o.value[1]["doc_number"] for o in outcomes}) == 6
    assert all(_open(line) == (5, 0) for line in lines)
    assert _po_state(po["id"])[0] == "ISSUED"


def test_double_click_creates_one_import_shipment() -> None:
    """J-01 수입판 — 같은 키·같은 본문 6스레드 동시 → 수입선적 1건·created 이벤트 1건·전 스레드 같은 응답"""
    po = _issued_po((10,))
    actor = make_user(RoleCode.TRADE)
    key = unique("imdbl")
    outcomes = run_concurrently(
        lambda _i: _create(actor, po["id"], [(po["lines"][0]["id"], 3)], key=key), workers=6
    )
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    assert all(o.value == outcomes[0].value for o in outcomes)
    assert scalar("SELECT count(*) FROM shipments WHERE po_id = :p", p=po["id"]) == 1
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'shipments.shipment.created'"
            " AND payload ->> 'po_id' = :p",
            p=str(po["id"]),
        )
        == 1
    )
    assert _open(po["lines"][0]["id"]) == (10, 7)


def _race_create_against_po_cancel() -> None:
    po = _issued_po((10,))
    trade, canceller = make_user(RoleCode.TRADE), make_user(RoleCode.TRADE)
    version = _po_state(po["id"])[1]

    def work(i: int) -> Any:
        if i == 0:
            return _create(trade, po["id"], [(po["lines"][0]["id"], 2)])
        return lifecycle.transition_purchase_order(
            actor=canceller,
            idempotency_key=unique("poc"),
            po_id=po["id"],
            to="CANCELLED",
            version=version,
            reason="동시 취소",
        )

    outcomes = run_concurrently(work, workers=2)
    _no_db_errors(outcomes)
    assert sum(o.ok for o in outcomes) == 1, [repr(o.error) for o in outcomes]
    create, cancel = outcomes
    live = int(
        scalar(
            "SELECT count(*) FROM shipments WHERE po_id = :p AND deleted_at IS NULL"
            " AND status NOT IN ('CANCELLED','EXPIRED')",
            p=po["id"],
        )
    )
    if create.ok:
        assert isinstance(cancel.error, AppError)
        assert cancel.error.code in (
            "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE",
            "COMMON.CONCURRENCY.VERSION_CONFLICT",
        )
        assert _po_state(po["id"])[0] == "ISSUED" and live == 1
    else:
        assert isinstance(create.error, AppError)
        assert create.error.code == "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
        assert _po_state(po["id"])[0] == "CANCELLED" and live == 0


def test_import_creation_racing_a_po_cancel_never_both_succeed() -> None:
    """J-04 수입판 — 수입선적 생성 vs 같은 PO 취소 동시(6회) → 둘 중 하나만 성공: 생성 먼저면 취소 409(SUCCESSOR_ALIVE·VERSION_CONFLICT),
    취소 먼저면 생성 409 DOCUMENT_NOT_CONSUMABLE — 취소된 PO 아래 살아 있는 수입선적 0(PO FOR SHARE ↔ 취소 FOR UPDATE 직렬화)"""
    for _ in range(6):
        _race_create_against_po_cancel()


def test_j08_a_failure_after_insert_rolls_the_import_creation_back_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J-08 수입판 — 채번·INSERT·탄생 이력·created 이벤트 뒤(수렴 호출 지점) 예외 → 선적·라인·당사자·이력·이벤트·멱등 키·채번 카운터 전부 원상,
    PO 행 무변경(업무 동작 1개 = TX 1개)"""
    po = _issued_po((10,))
    actor = make_user(RoleCode.TRADE)

    def counts() -> tuple[Any, ...]:
        return tuple(
            scalar(sql)
            for sql in (
                "SELECT count(*) FROM shipments",
                "SELECT count(*) FROM shipment_lines",
                "SELECT count(*) FROM shipment_parties",
                "SELECT count(*) FROM shipment_status_log",
                "SELECT count(*) FROM events",
                "SELECT count(*) FROM idempotency_keys",
                "SELECT COALESCE(SUM(last_number), 0) FROM doc_number_seq",
            )
        )

    before = (counts(), _po_state(po["id"]))

    class _Boom(RuntimeError):
        pass

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise _Boom("주입 실패")

    monkeypatch.setattr(shipment_flow, "converge_parent", boom)
    with pytest.raises(_Boom):
        _create(actor, po["id"], [(po["lines"][0]["id"], 4)])
    assert (counts(), _po_state(po["id"])) == before
    assert _open(po["lines"][0]["id"]) == (10, 10)


def test_a_held_po_lock_surfaces_as_lock_not_available_not_a_hang() -> None:
    """J-15 수입판 — 다른 TX가 PO 행을 FOR UPDATE로 잡은 동안 수입 생성은 무한 대기가 아니라 55P03(→ API 409 LOCK_BUSY)이고 부작용 0"""
    po = _issued_po((10,))
    actor = make_user(RoleCode.TRADE)
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(
            text("SELECT 1 FROM purchase_orders WHERE id = :i FOR UPDATE"), {"i": po["id"]}
        )
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            shipment_flow._plan_import(
                uow.session,
                actor,
                po["id"],
                import_body([(po["lines"][0]["id"], 2)]),
                lock=True,
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        tx.rollback()
        holder.close()
    assert scalar("SELECT count(*) FROM shipments WHERE po_id = :p", p=po["id"]) == 0


# ── 잠금 순서(LOCK_ORDER) 계측 — J-07 수입 경로 ─────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "sales_orders": "sales_orders",
    "purchase_orders": "purchase_orders",
    "shipments": "shipments",
    "shipment_parties": "shipment_children",
    "purchase_order_lines": "lines",
    "shipment_lines": "lines",
    "doc_number_seq": "doc_number_seq",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE_LOCK = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _lock_trace(work: Callable[[], object]) -> tuple[list[str], list[str]]:
    """work가 실행하는 SQL에서 (LOCK_ORDER 대상의 **첫 접촉** 순서, purchase_orders 헤더에 건 잠금 절들)."""
    order: list[str] = []
    po_modes: list[str] = []

    def listener(*args: object) -> None:
        statement = str(args[2])
        table: str | None = None
        clause = _LOCK_CLAUSE.search(statement)
        if clause:
            found = _FROM.search(statement)
            table = found.group(1) if found else None
            if table == "purchase_orders":
                po_modes.append(clause.group(0).upper())
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
    return order, po_modes


def _assert_follows_lock_order(sequence: list[str]) -> None:
    indexes = [LOCK_ORDER.index(name) for name in sequence]
    assert indexes == sorted(indexes), f"잠금 순서 위반: {sequence} (기준 {LOCK_ORDER})"


def test_every_import_shipment_operation_takes_locks_in_the_documented_order() -> None:
    """J-07 — 수입 생성(멱등→거래처→PO[**FOR SHARE만** — 승격 0]→PO 라인→채번)·라인 추가(멱등→PO→선적→라인)·수정·삭제(PO→선적…)·취소(멱등→PO→선적)가
    LOCK_ORDER(…PO → shipments → shipment_children → approvals → lines → seq)를 지킨다"""
    po = _issued_po((10, 10))
    actor = make_user(RoleCode.TRADE)
    a, b = (line["id"] for line in po["lines"])
    holder: dict[str, Any] = {}

    def create() -> None:
        holder["s"] = _create(actor, po["id"], [(a, 2)])[1]

    seen, po_modes = _lock_trace(create)
    assert seen == ["idempotency_keys", "partners", "purchase_orders", "lines", "doc_number_seq"], (
        seen
    )
    assert po_modes and all(mode == "FOR SHARE" for mode in po_modes), (
        po_modes
    )  # R-08 T2 — UPDATE·승격 0
    _assert_follows_lock_order(seen)
    shipment = holder["s"]

    def version() -> int:
        return int(scalar("SELECT version FROM shipments WHERE id = :i", i=shipment["id"]))

    def add_line() -> None:
        holder["s"] = shipment_flow.add_line(
            actor=actor,
            idempotency_key=unique("ila"),
            shipment_id=shipment["id"],
            payload={"version": version(), "source_line_id": b, "quantity": 1},
        )[1]

    seen, _modes = _lock_trace(add_line)
    assert seen == ["idempotency_keys", "purchase_orders", "shipments", "lines"], seen
    _assert_follows_lock_order(seen)
    line_b = next(ln for ln in holder["s"]["lines"] if ln["po_line_id"] == b)
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
        seen, _modes = _lock_trace(work)
        assert seen[:2] == ["purchase_orders", "shipments"], seen
        _assert_follows_lock_order(seen)
    seen, _modes = _lock_trace(
        lambda: shipment_flow.transition_shipment(
            actor=actor,
            idempotency_key=unique("imx"),
            shipment_id=shipment["id"],
            to="CANCELLED",
            version=version(),
            reason="동시성",
        )
    )
    assert seen == ["idempotency_keys", "purchase_orders", "shipments"], seen
    _assert_follows_lock_order(seen)
    assert _po_state(po["id"])[0] == "ISSUED"
