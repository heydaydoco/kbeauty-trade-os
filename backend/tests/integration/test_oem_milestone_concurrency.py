"""J. OEM 생산 일정(T13)·품목군 마일스톤 세트 동시성 — 잠금 순서 계측·PO 취소와의 직렬화·같은 키 더블클릭·최초 계획 경합·세트 중복 경합
(S3-2 PR-4c / design-integrated N-07·§2.11 / ADR-0078 / design-C C2·C14 J-01·J-07·J-10).

★ 순차 실행은 증거가 아니다 — 실제 스레드(각자 세션)를 Barrier로 동시에 출발시키거나, 다른 연결이 잠금을 쥔 채로 상대를 실제로 기다리게 한다.
  T13 = 멱등 → `purchase_orders FOR SHARE`(PO 무수정) → `milestones FOR UPDATE`. PO 취소는 PO를 `FOR UPDATE`로 잡으므로, 진행 중인 취소가 있으면
  OEM 쓰기는 기다렸다가 커밋된 취소를 보고 409 OWNER_NOT_ACTIVE를 낸다(잠금 없이 읽었다면 취소 전 스냅샷으로 기록이 새어 들어간다). 500·교착 0.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, text

from app.core.db.session import engine
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import lifecycle, milestone_flow, milestone_set_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.approvals import make_user
from tests.factories.shipments import scalar
from tests.factories.trade import raw_po, unique
from tests.support.concurrency import Outcome, run_concurrently
from tests.support.factories import create_item_profile

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]

ROUNDS = 5


def _code(outcome: Outcome) -> str:
    assert isinstance(outcome.error, AppError), repr(outcome.error)
    return str(outcome.error.code)


def _no_db_errors(outcomes: list[Outcome]) -> None:
    for outcome in outcomes:
        assert outcome.ok or isinstance(outcome.error, AppError), repr(outcome.error)


def _plan(
    actor: Any, po_id: int, milestone_type: str, payload: dict[str, Any], key: str = ""
) -> Any:
    return milestone_flow.record_oem_milestone_plan(
        actor=actor,
        idempotency_key=key or unique("op"),
        po_id=po_id,
        milestone_type=milestone_type,
        payload=payload,
    )


def _changes(po_id: int) -> int:
    return int(
        scalar(
            "SELECT count(*) FROM milestone_changes mc JOIN milestones m ON m.id = mc.milestone_id"
            " WHERE m.po_id = :p",
            p=po_id,
        )
    )


# ── 잠금 순서 계측 (J-07 — T13) ─────────────────────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "purchase_orders": "purchase_orders",
    "shipments": "shipments",
    "milestones": "shipment_children",
    "item_profile_milestone_types": "item_profile_milestone_types",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (NO KEY UPDATE|UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _lock_trace(work: Callable[[], object]) -> list[tuple[str, str]]:
    """work가 실행하는 SQL에서 잠금 대상의 **첫 접촉** 순서와 그 방식(행 잠금 절 = FOR …, 멱등 claim·쓰기 = WRITE)."""
    trace: list[tuple[str, str]] = []

    def listener(*args: object) -> None:
        statement = str(args[2])
        found = _LOCK_CLAUSE.search(statement)
        if found:
            source = _FROM.search(statement)
            table, mode = (source.group(1) if source else ""), f"FOR {found.group(1).upper()}"
        else:
            write = _WRITE.match(statement)
            if not write:
                return
            table, mode = write.group(1), "WRITE"
        name = _LOCK_TABLES.get(table)
        if name and name not in {seen for seen, _ in trace}:
            trace.append((name, mode))

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return trace


def _assert_follows_lock_order(trace: list[tuple[str, str]]) -> None:
    indexes = [LOCK_ORDER.index(name) for name, _ in trace if name in LOCK_ORDER]
    assert indexes == sorted(indexes), f"잠금 순서 위반: {trace} (기준 {LOCK_ORDER})"


def test_t13_locks_idempotency_then_po_for_share_then_the_milestone_row() -> None:
    """J-07(N-07) — OEM 계획(최초·롤오버)·실적 = 멱등 → **PO `FOR SHARE`**(UPDATE 아님 — PO 무수정) → 마일스톤 행 `FOR UPDATE`(shipment_children
    슬롯), `LOCK_ORDER` 색인 오름차순. 선적 헤더는 건드리지 않는다. 세트 추가 = 멱등 claim뿐(품목군·세트 행은 순서표 밖 — 전표 잠금 0)"""
    actor = make_user(RoleCode.TRADE)
    po_id = raw_po(po_kind="OEM_PRODUCTION")
    first = _lock_trace(lambda: _plan(actor, po_id, "FILLING", {"planned_on": today_kst()}))
    assert first == [
        ("idempotency_keys", "WRITE"),
        ("purchase_orders", "FOR SHARE"),
        ("shipment_children", "FOR UPDATE"),
    ], first
    _assert_follows_lock_order(first)
    version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
    rolled = _lock_trace(
        lambda: _plan(
            actor,
            po_id,
            "FILLING",
            {"planned_on": today_kst().replace(day=1), "version": version, "reason": "롤오버"},
        )
    )
    assert rolled == first, rolled
    version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
    actual = _lock_trace(
        lambda: milestone_flow.record_oem_milestone_actual(
            actor=actor,
            idempotency_key=unique("oa"),
            po_id=po_id,
            milestone_type="FILLING",
            payload={"actual_on": today_kst(), "version": version},
        )
    )
    assert actual == first, actual
    admin = make_user(RoleCode.ADMIN)
    profile = create_item_profile(unique("PRF"))
    added = _lock_trace(
        lambda: milestone_set_flow.add_profile_milestone_type(
            actor=admin, idempotency_key=unique("ps"), profile_id=profile, milestone_type="ETD"
        )
    )
    assert [name for name, _ in added] == ["idempotency_keys", "item_profile_milestone_types"]
    assert not {name for name, _ in added} & {"purchase_orders", "shipments", "shipment_children"}


# ── PO 취소와의 직렬화 (T13 FOR SHARE ↔ 취소 FOR UPDATE) ──────────────────────────────────


def _hold_cancel_in_flight(po_id: int) -> Any:
    """다른 연결이 PO를 `FOR UPDATE`로 잡고 CANCELLED로 바꾼 채 커밋하지 않는다(진행 중인 취소 TX — lifecycle과 같은 잠금 모드)."""
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(
        text("SELECT id FROM purchase_orders WHERE id = :p FOR UPDATE"), {"p": po_id}
    )
    connection.execute(
        text(
            "UPDATE purchase_orders SET status = 'CANCELLED', version = version + 1 WHERE id = :p"
        ),
        {"p": po_id},
    )
    return connection, transaction


def test_an_oem_write_waits_for_an_in_flight_po_cancel_and_then_refuses() -> None:
    """N-07 — 취소 TX가 PO를 `FOR UPDATE`로 쥔 동안 OEM 계획은 **기다리고**(잠금 없는 읽기면 취소 전 스냅샷으로 바로 기록된다), 취소가 커밋되면
    잠금 뒤 읽은 최신 상태로 409 OWNER_NOT_ACTIVE — 취소된 PO에 새 생산 일정 0"""
    actor = make_user(RoleCode.TRADE)
    for _ in range(3):
        po_id = raw_po(po_kind="OEM_PRODUCTION")
        connection, transaction = _hold_cancel_in_flight(po_id)
        holder: dict[str, Any] = {}

        def write(po_id: int = po_id, holder: dict[str, Any] = holder) -> None:
            try:
                holder["value"] = _plan(actor, po_id, "PACKING", {"planned_on": today_kst()})
            except BaseException as exc:
                holder["error"] = exc

        worker = threading.Thread(target=write)
        worker.start()
        time.sleep(1.0)
        waiting = worker.is_alive()
        transaction.commit()
        connection.close()
        worker.join(timeout=10)
        assert waiting, "OEM 쓰기가 진행 중인 PO 취소를 기다리지 않았다(PO FOR SHARE 누락)"
        error = holder.get("error")
        assert isinstance(error, AppError), holder
        assert str(error.code) == "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
        assert scalar("SELECT count(*) FROM milestones WHERE po_id = :p", p=po_id) == 0


def test_an_oem_actual_and_a_po_cancel_race_without_errors() -> None:
    """실제 동시 — OEM 실적 vs PO 취소(사람 전이 lifecycle) 5라운드: 취소는 늘 성공(OEM 기록은 PO 취소를 막지 않는다 — 설계 침묵, 부채),
    실적은 먼저 잡으면 성공·늦으면 409 OWNER_NOT_ACTIVE. 500·교착 0, 실패한 실적은 이력 0"""
    actor = make_user(RoleCode.TRADE)
    seen: set[str] = set()
    for _ in range(ROUNDS):
        po_id = raw_po(po_kind="OEM_PRODUCTION")
        _plan(actor, po_id, "OUTGOING_INSPECTION", {"planned_on": today_kst()})
        milestone_version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
        po_version = int(scalar("SELECT version FROM purchase_orders WHERE id = :p", p=po_id))

        def work(
            index: int, po_id: int = po_id, mv: int = milestone_version, pv: int = po_version
        ) -> Any:
            if index == 0:
                return milestone_flow.record_oem_milestone_actual(
                    actor=actor,
                    idempotency_key=unique("ra"),
                    po_id=po_id,
                    milestone_type="OUTGOING_INSPECTION",
                    payload={"actual_on": today_kst(), "version": mv},
                )
            return lifecycle.transition_purchase_order(
                actor=actor,
                idempotency_key=unique("rc"),
                po_id=po_id,
                to="CANCELLED",
                version=pv,
                reason="동시성",
            )

        outcomes = run_concurrently(work, workers=2)
        _no_db_errors(outcomes)
        assert outcomes[1].ok, repr(outcomes[1].error)
        if outcomes[0].ok:
            seen.add("actual-first")
            assert _changes(po_id) == 2
        else:
            seen.add("cancel-first")
            assert _code(outcomes[0]) == "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
            assert _changes(po_id) == 1
        assert scalar("SELECT status FROM purchase_orders WHERE id = :p", p=po_id) == "CANCELLED"
    assert seen


# ── 같은 키 더블클릭·최초 계획 경합 (J-01·J-10) ─────────────────────────────────────────


def test_a_double_click_oem_rollover_writes_one_history_row_with_one_change_id() -> None:
    """J-01·J-14(R-19 승계) — 같은 Idempotency-Key OEM 롤오버 6스레드 동시 → 이력 PLAN_CHANGED 1행·아웃박스 1건·전 스레드 같은 change.id"""
    actor = make_user(RoleCode.TRADE)
    po_id = raw_po(po_kind="OEM_PRODUCTION")
    _plan(actor, po_id, "RAW_MATERIAL_READY", {"planned_on": today_kst()})
    version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
    moved = today_kst().replace(day=2 if today_kst().day == 1 else 1)
    key = unique("dbl")
    outcomes = run_concurrently(
        lambda _i: _plan(
            actor,
            po_id,
            "RAW_MATERIAL_READY",
            {"planned_on": moved, "reason": "원료 지연", "version": version},
            key,
        ),
        workers=6,
    )
    _no_db_errors(outcomes)
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    assert len({o.value[1]["change"]["id"] for o in outcomes}) == 1
    assert _changes(po_id) == 2  # PLAN_SET 1 + PLAN_CHANGED 1
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'shipments.milestone.changed'"
            " AND aggregate_type = 'purchase_orders' AND aggregate_id = :p",
            p=po_id,
        )
        == 2
    )


def test_two_first_oem_plans_on_the_same_type_leave_one_row() -> None:
    """J-10(T13) — PO `FOR SHARE`는 공유 잠금이라 최초 계획 2건(다른 키)이 함께 들어온다: (PO, 종류) 부분 유니크가 둘째를 409 DUPLICATE_TYPE으로
    거른다(번역표 — 500 0). 행 1·이력 1"""
    actor = make_user(RoleCode.TRADE)
    for _ in range(ROUNDS):
        po_id = raw_po(po_kind="OEM_PRODUCTION")
        outcomes = run_concurrently(
            lambda i, po_id=po_id: _plan(
                actor, po_id, "FILLING", {"planned_on": today_kst().replace(day=10 + i)}
            ),
            workers=2,
        )
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1, [repr(o.error) for o in outcomes]
        assert _code(next(o for o in outcomes if not o.ok)) in {
            "SHIPMENTS.MILESTONE.DUPLICATE_TYPE",
            "COMMON.CONCURRENCY.VERSION_CONFLICT",
        }
        assert scalar("SELECT count(*) FROM milestones WHERE po_id = :p", p=po_id) == 1
        assert _changes(po_id) == 1


def test_two_admins_adding_the_same_set_type_leave_one_row() -> None:
    """J — 같은 품목군·같은 종류 동시 추가 2건(다른 키): 무잠금 peek를 둘 다 통과해도 부분 유니크 번역이 둘째를 409 DUPLICATE_TYPE으로(500 0)"""
    admin = make_user(RoleCode.ADMIN)
    for _ in range(ROUNDS):
        profile = create_item_profile(unique("PRF"))
        outcomes = run_concurrently(
            lambda _i, profile=profile: milestone_set_flow.add_profile_milestone_type(
                actor=admin, idempotency_key=unique("cs"), profile_id=profile, milestone_type="ETA"
            ),
            workers=2,
        )
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1
        assert _code(next(o for o in outcomes if not o.ok)) == "SHIPMENTS.MILESTONE.DUPLICATE_TYPE"
        assert (
            scalar(
                "SELECT count(*) FROM item_profile_milestone_types"
                " WHERE profile_id = :p AND deleted_at IS NULL",
                p=profile,
            )
            == 1
        )
