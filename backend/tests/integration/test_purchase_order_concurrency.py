"""J. PO 동시성 — 생성 더블클릭=1건·채번 직렬화·공급사 유형 해제 vs 생성·취소 경합·복제 경합·롤백 원자성·잠금 순서 (S3-1 PR-8a / ADR-0057·0059 / design-F F2·F3 (f) / design-B B3·B8).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다.
  이 파일의 테스트는 커밋을 동반하므로 병렬(pytest-xdist)로 돌리지 않는다. 결과 불변식(유형 없는 PO 0건·번호 중복/결번 0·이중 전이 0)을 본다.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.core.db.session import engine, owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.purchase_orders import service as po_service
from app.modules.trade_chain import lifecycle
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.trade import (
    create_purchase_priced_sku,
    create_supplier,
    po_payload,
    raw_po,
    unique,
)
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_user

pytestmark = pytest.mark.group_j


def _actor() -> AuthenticatedUser:
    address = f"{unique('po-conc')}@example.com"
    user_id = create_user(address, roles=(RoleCode.TRADE,))
    return AuthenticatedUser(
        id=user_id,
        email=address,
        display_name="동시성",
        roles=frozenset({RoleCode.TRADE}),
        session_id=0,
    )


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _create(
    actor: AuthenticatedUser, payload: dict[str, Any], key: str | None = None
) -> tuple[int, dict[str, Any]]:
    return po_service.create_purchase_order(
        actor=actor, idempotency_key=key or unique("k"), payload=payload
    )


def _cancel(
    actor: AuthenticatedUser, po_id: int, version: int, key: str | None = None
) -> tuple[int, dict[str, Any]]:
    return lifecycle.transition_purchase_order(
        actor=actor,
        idempotency_key=key or unique("c"),
        po_id=po_id,
        to="CANCELLED",
        version=version,
        reason="동시성",
    )


def _payload(supplier: int | None = None) -> dict[str, Any]:
    return po_payload(
        supplier or create_supplier(),
        [create_purchase_priced_sku(amount=300)],
    )


# ── 생성 더블클릭 · 채번 ──────────────────────────────────────────────────────


def test_double_click_with_the_same_key_creates_exactly_one_po() -> None:
    """같은 키·같은 본문 6스레드 동시 생성 → PO 1건·번호 1개·created 이벤트 1건·탄생 이력 1행, 전 스레드가 같은 응답을 받는다(발주 이중 확정 0)"""
    actor = _actor()
    payload = _payload()
    key = unique("dbl")
    outcomes = run_concurrently(lambda _i: _create(actor, payload, key), workers=6)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes if not o.ok]
    assert len({o.value[1]["id"] for o in outcomes}) == 1
    assert {o.value[0] for o in outcomes} == {201}
    assert _scalar("SELECT count(*) FROM purchase_orders") == 1
    assert _scalar("SELECT count(*) FROM purchase_order_status_log") == 1
    assert (
        _scalar(
            "SELECT count(*) FROM events WHERE event_type = 'purchase_orders.purchase_order.created'"
        )
        == 1
    )
    assert _scalar("SELECT count(*) FROM doc_number_seq WHERE prefix = 'PO'") == 1


def test_concurrent_creations_get_unique_gapless_numbers() -> None:
    """서로 다른 키로 8스레드 동시 생성 → PO 8건·번호 중복 0·결번 0(채번 카운터 행 잠금이 직렬화한다 — MAX+1 아님)"""
    actor = _actor()
    outcomes = run_concurrently(lambda _i: _create(actor, _payload()), workers=8)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes if not o.ok]
    numbers = sorted(o.value[1]["doc_number"] for o in outcomes)
    assert len(set(numbers)) == 8
    assert [int(n.rsplit("-", 1)[1]) for n in numbers] == list(range(1, 9))
    assert _scalar("SELECT count(*) FROM purchase_orders") == 8


# ── 공급사 유형 해제 vs 생성 (FOR KEY SHARE ↔ FOR UPDATE) ─────────────────────────


def _hold_partner(
    supplier: int, *, mode: str, release_type: bool, hold_seconds: float, started: threading.Event
) -> None:
    """공급사 행을 `mode`로 잡고 hold_seconds 동안 들고 있다가(선택: 유형 링크 해제) 커밋한다 — 유형 해제 임포트(FOR UPDATE)·여신 직렬화(NO KEY UPDATE)의 대역."""
    with owner_engine.connect() as connection, connection.begin():
        connection.execute(
            text(f"SELECT 1 FROM partners WHERE id = :p FOR {mode}"), {"p": supplier}
        )
        started.set()
        time.sleep(hold_seconds)
        if release_type:
            connection.execute(
                text("UPDATE partner_type_links SET deleted_at = now() WHERE partner_id = :p"),
                {"p": supplier},
            )


def test_creation_waits_for_an_in_flight_type_release_and_then_fails_closed() -> None:
    """유형 해제(FOR UPDATE)가 먼저 진행 중이면 생성은 KEY SHARE에서 **기다렸다가** 커밋 뒤의 유형 없음을 보고 422다 — 유형 없는 채로 PO가 생성되는 상태가 없다"""
    actor = _actor()
    supplier = create_supplier(types=("SUPPLIER",))
    payload = _payload(supplier)
    started = threading.Event()
    holder = threading.Thread(
        target=_hold_partner,
        kwargs={
            "supplier": supplier,
            "mode": "UPDATE",
            "release_type": True,
            "hold_seconds": 0.8,
            "started": started,
        },
    )
    holder.start()
    assert started.wait(5)
    began = time.monotonic()
    with pytest.raises(AppError) as caught:
        _create(actor, payload)
    waited = time.monotonic() - began
    holder.join()
    assert (
        caught.value.code == "COMMON.VALIDATION.INVALID_FIELD" and caught.value.status_code == 422
    )
    assert waited >= 0.5, f"생성이 해제를 기다리지 않았다({waited:.2f}s) — 잠금이 직렬화하지 않는다"
    assert _scalar("SELECT count(*) FROM purchase_orders") == 0


def test_a_type_release_waits_for_an_in_flight_creation_holding_key_share() -> None:
    """생성이 공급사 행 KEY SHARE를 잡고 있는 동안 유형 해제(FOR UPDATE)는 **그 생성이 끝날 때까지 기다린다** — 생성이 본 유형이 커밋 전에 사라질 수 없다(두 잠금의 충돌 실증)"""
    supplier = create_supplier(types=("SUPPLIER",))
    started = threading.Event()
    holder = threading.Thread(
        target=_hold_partner,
        kwargs={
            "supplier": supplier,
            "mode": "KEY SHARE",
            "release_type": False,
            "hold_seconds": 0.8,
            "started": started,
        },
    )
    holder.start()
    assert started.wait(5)
    began = time.monotonic()
    with owner_engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT 1 FROM partners WHERE id = :p FOR UPDATE"), {"p": supplier})
    waited = time.monotonic() - began
    holder.join()
    assert waited >= 0.5


def test_creation_is_not_blocked_by_the_credit_serialization_lock() -> None:
    """여신 직렬화 경로의 `FOR NO KEY UPDATE`와 PO 생성의 `FOR KEY SHARE`는 충돌하지 않는다 — 여신 잠금이 잡혀 있어도 생성이 즉시 성공한다(X-37)"""
    actor = _actor()
    supplier = create_supplier(types=("SUPPLIER",))
    started = threading.Event()
    holder = threading.Thread(
        target=_hold_partner,
        kwargs={
            "supplier": supplier,
            "mode": "NO KEY UPDATE",
            "release_type": False,
            "hold_seconds": 1.5,
            "started": started,
        },
    )
    holder.start()
    assert started.wait(5)
    began = time.monotonic()
    status, body = _create(actor, _payload(supplier))
    elapsed = time.monotonic() - began
    holder.join()
    assert status == 201 and body["status"] == "ISSUED"
    assert elapsed < 1.0, f"여신 잠금에 막혔다({elapsed:.2f}s)"


def test_preview_never_waits_for_the_supplier_lock() -> None:
    """미리보기는 공급사를 잠그지 않는다 — 유형 해제(FOR UPDATE)가 진행 중이어도 즉시 응답한다(읽기 전용)"""
    actor = _actor()
    supplier = create_supplier(types=("SUPPLIER",))
    started = threading.Event()
    holder = threading.Thread(
        target=_hold_partner,
        kwargs={
            "supplier": supplier,
            "mode": "UPDATE",
            "release_type": False,
            "hold_seconds": 1.2,
            "started": started,
        },
    )
    holder.start()
    assert started.wait(5)
    began = time.monotonic()
    body = po_service.preview_purchase_order(actor=actor, payload=_payload(supplier))
    elapsed = time.monotonic() - began
    holder.join()
    assert body["total_cost"] == 3000 and elapsed < 1.0


# ── 전이 경합 ──────────────────────────────────────────────────────────────────


def test_two_concurrent_cancels_yield_one_success_and_one_conflict() -> None:
    """같은 PO·같은 version을 서로 다른 키로 동시에 취소 → 정확히 1건 성공·1건 409, 취소 이력 1행·이벤트 1건(이중 전이 0)"""
    actor = _actor()
    _status, po = _create(actor, _payload())
    outcomes = run_concurrently(lambda _i: _cancel(actor, po["id"], po["version"]), workers=2)
    assert sorted(o.ok for o in outcomes) == [False, True]
    loser = next(o for o in outcomes if not o.ok)
    assert isinstance(loser.error, AppError) and loser.error.status_code == 409
    assert (
        _scalar("SELECT count(*) FROM purchase_order_status_log WHERE to_status = 'CANCELLED'") == 1
    )
    assert (
        _scalar(
            "SELECT count(*) FROM events WHERE event_type = 'purchase_orders.purchase_order.status_changed'"
        )
        == 1
    )
    assert _scalar("SELECT status FROM purchase_orders WHERE id = :i", i=po["id"]) == "CANCELLED"


def test_supplier_confirmation_versus_cancel_leaves_a_consistent_state() -> None:
    """공급사 확인(OC)과 취소가 동시에 들어오면 한쪽만 성공하고 최종 상태·OC 열·이력이 서로 모순되지 않는다(DB CHECK가 모순 상태를 막는다)"""
    actor = _actor()
    _status, po = _create(actor, _payload())

    def worker(index: int) -> Any:
        if index == 0:
            return lifecycle.transition_purchase_order(
                actor=actor,
                idempotency_key=unique("oc"),
                po_id=po["id"],
                to="SUPPLIER_CONFIRMED",
                version=po["version"],
                reason=None,
                oc_received_on=today_kst(),
            )
        return _cancel(actor, po["id"], po["version"])

    outcomes = run_concurrently(worker, workers=2)
    winners = [o for o in outcomes if o.ok]
    assert len(winners) == 1 and isinstance(next(o for o in outcomes if not o.ok).error, AppError)
    with (
        owner_engine.connect() as connection
    ):  # 연결을 닫지 않으면 teardown의 TRUNCATE가 잠금 대기로 실패한다
        row = tuple(
            connection.execute(
                text(
                    "SELECT status, oc_received_on IS NOT NULL FROM purchase_orders WHERE id = :i"
                ),
                {"i": po["id"]},
            ).one()
        )
    assert row in (("SUPPLIER_CONFIRMED", True), ("CANCELLED", False))
    assert _scalar("SELECT count(*) FROM purchase_order_status_log") == 2  # 탄생 + 성공한 전이 1행


# ── 복제 경합 ──────────────────────────────────────────────────────────────────


def test_two_copies_of_the_same_cancelled_po_yield_exactly_one() -> None:
    """같은 취소 PO를 서로 다른 키로 동시에 복제 → 1건 성공·1건 409 COPY.SOURCE_NOT_ELIGIBLE(원본 행 FOR UPDATE가 직렬화 — 부분 유니크 위반 500이 아니다)"""
    actor = _actor()
    supplier = create_supplier()
    source = raw_po("CANCELLED", supplier_partner_id=supplier)
    payload = {**_payload(supplier), "copied_from_id": source}
    outcomes = run_concurrently(lambda _i: _create(actor, payload), workers=2)
    assert sorted(o.ok for o in outcomes) == [False, True]
    loser = next(o for o in outcomes if not o.ok)
    assert (
        isinstance(loser.error, AppError)
        and loser.error.code == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    )
    assert _scalar("SELECT count(*) FROM purchase_orders WHERE copied_from_id = :s", s=source) == 1


# ── 롤백 원자성 ────────────────────────────────────────────────────────────────


def test_a_failure_after_numbering_rolls_everything_back_including_the_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """채번 뒤(탄생 기록 단계)에서 실패하면 PO·라인·이력·이벤트·멱등 행·번호 카운터가 **전부** 롤백된다 — 결번 0(다음 생성이 -0001을 받는다)"""
    actor = _actor()
    payload = _payload()

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("탄생 기록 실패 시뮬레이션")

    monkeypatch.setattr(po_service, "record_birth", boom)
    with pytest.raises(RuntimeError):
        _create(actor, payload)
    monkeypatch.undo()
    for table in (
        "purchase_orders",
        "purchase_order_lines",
        "purchase_order_status_log",
        "idempotency_keys",
    ):
        assert _scalar(f"SELECT count(*) FROM {table}") == 0, table
    assert _scalar("SELECT count(*) FROM events WHERE event_type LIKE 'purchase_orders.%'") == 0
    assert _scalar("SELECT count(*) FROM doc_number_seq WHERE prefix = 'PO'") == 0
    status, body = _create(actor, payload)
    assert status == 201 and body["doc_number"].endswith("-0001")


def test_a_held_po_lock_surfaces_as_lock_not_available_not_a_hang() -> None:
    """다른 트랜잭션이 PO 행을 잠근 상태에서 전이하면 무한 대기가 아니라 55P03(잠금 대기 초과)이 난다 — API는 409 LOCK_BUSY로 번역"""
    actor = _actor()
    _status, po = _create(actor, _payload())
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(
            text("SELECT 1 FROM purchase_orders WHERE id = :i FOR UPDATE"), {"i": po["id"]}
        )
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            from app.modules.trade_chain.chain_ops import lock_chain
            from app.modules.trade_docs.constants import DocKind

            lock_chain(uow.session, DocKind.PURCHASE_ORDER, po["id"])
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        tx.rollback()
        holder.close()


# ── 잠금 순서(LOCK_ORDER) 계측 ─────────────────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "purchase_orders": "purchase_orders",
    "purchase_order_lines": "lines",
    "doc_number_seq": "doc_number_seq",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE_LOCK = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _first_lock_sequence(work: Callable[[], object]) -> list[str]:
    """work가 실행하는 SQL에서 LOCK_ORDER 대상의 **첫 접촉** 순서를 기록한다(행 잠금 절 또는 멱등·채번 쓰기)."""
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


def test_the_lock_order_probe_flags_a_reversed_order() -> None:
    """공회전 방지 — 계측이 잠금을 실제로 기록하고(PO 생성이 멱등·공급사·채번을 만진다), 뒤집힌 순서는 위반으로 판정한다"""
    actor = _actor()
    payload = _payload()
    seen = _first_lock_sequence(lambda: _create(actor, payload))
    assert {"idempotency_keys", "partners", "doc_number_seq"} <= set(seen), seen
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["purchase_orders", "partners"])
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["doc_number_seq", "purchase_orders"])


def test_every_po_operation_takes_locks_in_the_documented_order() -> None:
    """생성(멱등→공급사→채번 마지막)·복제 생성(공급사→원본 PO→채번)·전이(멱등→PO)가 LOCK_ORDER를 지킨다 — 채번은 항상 마지막"""
    actor = _actor()
    supplier = create_supplier()
    holder: dict[str, Any] = {}

    def create() -> None:
        _s, holder["po"] = _create(actor, _payload(supplier))

    seen = _first_lock_sequence(create)
    assert seen[:2] == ["idempotency_keys", "partners"] and seen[-1] == "doc_number_seq", seen
    _assert_follows_lock_order(seen)
    po = holder["po"]
    cancelled = _first_lock_sequence(lambda: _cancel(actor, po["id"], po["version"]))
    assert cancelled == ["idempotency_keys", "purchase_orders"], cancelled
    _assert_follows_lock_order(cancelled)
    source = raw_po("CANCELLED", supplier_partner_id=supplier)

    def copy() -> None:
        _create(actor, {**_payload(supplier), "copied_from_id": source})

    copied = _first_lock_sequence(copy)
    assert (
        copied[:3] == ["idempotency_keys", "partners", "purchase_orders"]
        and copied[-1] == "doc_number_seq"
    ), copied
    _assert_follows_lock_order(copied)
