"""J. OEM 생산 일정(T13)·품목군 마일스톤 세트 동시성 — 잠금 순서 계측·PO 취소와의 직렬화·같은 키 더블클릭·최초 계획 경합·세트 중복 경합
(S3-2 PR-4c / design-integrated N-07·§2.11 / ADR-0078 / design-C C2·C14 J-01·J-07·J-10).

★ 순차 실행은 증거가 아니다 — 실제 스레드(각자 세션)를 Barrier로 동시에 출발시키거나, 한쪽이 잠금을 쥔 채 멈춘 동안 상대가 **실제로 잠금을
  기다리는 것을 `pg_locks`(미부여 행 — 대기 pid 특정)로 관측**한 뒤 풀어 준다(sleep·is_alive 추정 0 — test_order_board_saved_filters 선례).
  T13 = 멱등 → `purchase_orders FOR SHARE`(PO 무수정) → `milestones FOR UPDATE`. PO 취소는 PO를 `FOR UPDATE`로 잡으므로, 진행 중인 취소가 있으면
  OEM 쓰기는 기다렸다가 커밋된 취소를 보고 409 OWNER_NOT_ACTIVE를 내고, OEM 쓰기가 PO를 쥔 동안에는 취소가 기다린다. 500·교착 0.
  (PR-4c 적대 검토 반영 — KST 고정·달력 무관 롤오버 값·J-10 번역 경로 결정화·대기 증거 pg_locks·두 순서 결정적 분리.)
"""

from __future__ import annotations

import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event, text

from app.core.db.session import engine
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.idempotency import service as idempotency_service
from app.modules.identity.models import RoleCode
from app.modules.shipments import service as shipments_service
from app.modules.trade_chain import lifecycle, milestone_flow, milestone_set_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.approvals import make_user
from tests.factories.shipments import scalar
from tests.factories.trade import raw_po, unique
from tests.support.concurrency import Outcome, run_concurrently
from tests.support.factories import create_item_profile
from tests.support.kst import pin_today_kst

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]

ROUNDS = 5
#: 잠금 대기 관측 상한(초) — 앱 역할 lock_timeout(5s)보다 짧게 끝나야 대기 쪽이 55P03으로 죽지 않는다.
WAIT_OBSERVE_SECONDS = 4.0


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정(PR-4c 적대 검토 반영 ①) — 앱 import 지점과 이 시험 모듈의 `today_kst`를 같은 날로(스레드 공유 — 모듈 속성)."""
    pin_today_kst(monkeypatch, sys.modules[__name__])


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
    holder: dict[str, Any] = {}

    def rollover() -> None:
        holder["body"] = _plan(
            actor,
            po_id,
            "FILLING",
            {
                "planned_on": today_kst()
                + timedelta(days=1),  # 늘 다른 날(달력 무관 — 같은 날이면 no-op)
                "version": version,
                "reason": "롤오버",
            },
        )[1]

    rolled = _lock_trace(rollover)
    assert rolled == first, rolled
    assert holder["body"]["change"]["change_kind"] == "PLAN_CHANGED"
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


def _record_pid_of(monkeypatch: pytest.MonkeyPatch, key: str) -> dict[str, int]:
    """멱등 키 `key`로 들어온 트랜잭션의 백엔드 pid를 기록한다(claim은 TX 첫 문장 — 그 연결이 뒤의 잠금도 잡는다)."""
    pids: dict[str, int] = {}
    original = idempotency_service.claim

    def recording(session: Any, **kwargs: Any) -> Any:
        if kwargs.get("key") == key:
            pids["pid"] = int(session.execute(text("SELECT pg_backend_pid()")).scalar_one())
        return original(session, **kwargs)

    monkeypatch.setattr(idempotency_service, "claim", recording)
    return pids


def _observe_lock_wait(pids: dict[str, int]) -> bool:
    """기록된 pid가 **부여되지 않은 잠금**을 기다리는 것을 pg_locks로 본다(대기 증거 — 추정 아님). 상한 안에 못 보면 False."""
    deadline = time.monotonic() + WAIT_OBSERVE_SECONDS
    while time.monotonic() < deadline:
        if "pid" in pids:
            with engine.connect() as connection:
                waiting = connection.execute(
                    text("SELECT count(*) FROM pg_locks WHERE pid = :p AND NOT granted"),
                    {"p": pids["pid"]},
                ).scalar_one()
            if waiting:
                return True
        time.sleep(0.05)
    return False


def _run_in_thread(work: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    holder: dict[str, Any] = {}

    def run() -> None:
        try:
            holder["value"] = work()
        except BaseException as exc:  # 결과로 판정한다
            holder["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, holder


def test_a_first_oem_plan_waits_for_an_in_flight_po_cancel_and_then_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N-07 — 취소 TX가 PO를 `FOR UPDATE`로 쥔 동안 OEM **최초 계획**(INSERT 경로)은 잠금을 실제로 기다리고(pg_locks 미부여 관측), 취소가
    커밋되면 잠금 뒤 읽은 최신 상태로 409 OWNER_NOT_ACTIVE — 취소된 PO에 새 생산 일정 0. (INSERT는 FK 검사도 PO 행을 잡으므로 대기 자체는
    FOR SHARE 누락을 구분하지 못한다 — 이 변형이 잡는 결함은 '잠금 뒤 상태 재판정 누락'이고, FOR SHARE 누락은 아래 UPDATE 변형이 잡는다)"""
    actor = make_user(RoleCode.TRADE)
    for round_no in range(3):
        po_id = raw_po(po_kind="OEM_PRODUCTION")
        key = f"inflight-insert-{po_id}-{round_no}"
        pids = _record_pid_of(monkeypatch, key)
        connection, transaction = _hold_cancel_in_flight(po_id)
        try:
            thread, holder = _run_in_thread(
                lambda po_id=po_id, key=key: _plan(
                    actor, po_id, "PACKING", {"planned_on": today_kst()}, key
                )
            )
            waited = _observe_lock_wait(pids)
        finally:
            transaction.commit()
            connection.close()
        thread.join(15)
        assert waited, "OEM 최초 계획이 진행 중인 PO 취소의 잠금을 기다리지 않았다"
        error = holder.get("error")
        assert isinstance(error, AppError), (
            "취소 커밋 뒤에도 기록됐다 — 잠금 뒤 PO 상태를 다시 판정하지 않는다",
            holder,
        )
        assert str(error.code) == "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
        assert scalar("SELECT count(*) FROM milestones WHERE po_id = :p", p=po_id) == 0


def test_an_oem_rollover_waits_for_an_in_flight_po_cancel_and_then_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N-07 — 행이 이미 있는 **롤오버(UPDATE 경로 — FK 검사 없음)**도 진행 중 취소의 PO `FOR UPDATE`를 실제로 기다린다(pg_locks 관측) — 이 대기의
    유일한 원천이 T13 `purchase_orders FOR SHARE`다(빼면 기다리지 않고 취소 전 스냅샷으로 롤오버가 기록된다). 커밋 뒤 409 OWNER_NOT_ACTIVE,
    계획 값·이력 무변경"""
    actor = make_user(RoleCode.TRADE)
    for round_no in range(3):
        po_id = raw_po(po_kind="OEM_PRODUCTION")
        _plan(actor, po_id, "PACKING", {"planned_on": today_kst()})
        version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
        key = f"inflight-update-{po_id}-{round_no}"
        pids = _record_pid_of(monkeypatch, key)
        connection, transaction = _hold_cancel_in_flight(po_id)
        try:
            thread, holder = _run_in_thread(
                lambda po_id=po_id, key=key, version=version: _plan(
                    actor,
                    po_id,
                    "PACKING",
                    {
                        "planned_on": today_kst() + timedelta(days=3),
                        "version": version,
                        "reason": "포장 지연",
                    },
                    key,
                )
            )
            waited = _observe_lock_wait(pids)
        finally:
            transaction.commit()
            connection.close()
        thread.join(15)
        assert waited, (
            "롤오버(UPDATE)가 진행 중인 PO 취소를 기다리지 않았다 — T13 PO FOR SHARE 누락"
        )
        error = holder.get("error")
        assert isinstance(error, AppError), holder
        assert str(error.code) == "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
        assert scalar("SELECT planned_on FROM milestones WHERE po_id = :p", p=po_id) == today_kst()
        assert _changes(po_id) == 1


def test_a_po_cancel_waits_while_an_oem_actual_holds_the_po_then_both_land(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """순서 ① 실적 먼저 — OEM 실적 TX가 PO `FOR SHARE`를 쥔 채 멈춘 동안 PO 취소(lifecycle `FOR UPDATE`)가 실제로 기다리고(pg_locks 관측),
    실적이 커밋되면 취소가 이어서 성공한다(OEM 실적은 PO 취소를 막지 않는다 — 설계 침묵, 부채 R-4c-1). 이력 2(설정·실적)·PO CANCELLED·500 0"""
    actor = make_user(RoleCode.TRADE)
    po_id = raw_po(po_kind="OEM_PRODUCTION")
    _plan(actor, po_id, "OUTGOING_INSPECTION", {"planned_on": today_kst()})
    milestone_version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
    po_version = int(scalar("SELECT version FROM purchase_orders WHERE id = :p", p=po_id))
    cancel_key = f"cancel-after-actual-{po_id}"
    pids = _record_pid_of(monkeypatch, cancel_key)
    holding, release = threading.Event(), threading.Event()
    original = milestone_flow._require_po_active

    def pausing(po: Any) -> None:
        original(po)  # PO FOR SHARE를 쥔 뒤 — 취소가 잠금 대기에 들어갈 때까지 멈춘다
        holding.set()
        assert release.wait(10)

    monkeypatch.setattr(milestone_flow, "_require_po_active", pausing)
    actual_thread, actual = _run_in_thread(
        lambda: milestone_flow.record_oem_milestone_actual(
            actor=actor,
            idempotency_key=unique("ra"),
            po_id=po_id,
            milestone_type="OUTGOING_INSPECTION",
            payload={"actual_on": today_kst(), "version": milestone_version},
        )
    )
    assert holding.wait(10)
    cancel_thread, cancel = _run_in_thread(
        lambda: lifecycle.transition_purchase_order(
            actor=actor,
            idempotency_key=cancel_key,
            po_id=po_id,
            to="CANCELLED",
            version=po_version,
            reason="동시성",
        )
    )
    waited = _observe_lock_wait(pids)
    release.set()
    actual_thread.join(15)
    cancel_thread.join(15)
    assert waited, "PO 취소가 OEM 실적의 PO FOR SHARE를 기다리지 않았다"
    assert "error" not in actual, actual
    assert actual["value"][1]["change"]["change_kind"] == "ACTUAL_RECORDED"
    assert "error" not in cancel, cancel
    assert scalar("SELECT status FROM purchase_orders WHERE id = :p", p=po_id) == "CANCELLED"
    assert _changes(po_id) == 2


def test_an_oem_actual_waits_for_an_in_flight_lifecycle_cancel_and_then_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """순서 ② 취소 먼저 — 실제 PO 취소(lifecycle)가 PO `FOR UPDATE`를 쥔 채 멈춘 동안 OEM 실적이 잠금을 실제로 기다리고(pg_locks 관측), 취소가
    커밋되면 409 OWNER_NOT_ACTIVE — 이력은 설정 1건 그대로·PO CANCELLED·500 0"""
    actor = make_user(RoleCode.TRADE)
    po_id = raw_po(po_kind="OEM_PRODUCTION")
    _plan(actor, po_id, "OUTGOING_INSPECTION", {"planned_on": today_kst()})
    milestone_version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
    po_version = int(scalar("SELECT version FROM purchase_orders WHERE id = :p", p=po_id))
    actual_key = f"actual-after-cancel-{po_id}"
    pids = _record_pid_of(monkeypatch, actual_key)
    holding, release = threading.Event(), threading.Event()
    original = lifecycle.record_transition

    def pausing(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)  # PO FOR UPDATE + CANCELLED 대입 뒤, 커밋 전에 멈춘다
        holding.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(lifecycle, "record_transition", pausing)
    cancel_thread, cancel = _run_in_thread(
        lambda: lifecycle.transition_purchase_order(
            actor=actor,
            idempotency_key=unique("rc"),
            po_id=po_id,
            to="CANCELLED",
            version=po_version,
            reason="동시성",
        )
    )
    assert holding.wait(10)
    actual_thread, actual = _run_in_thread(
        lambda: milestone_flow.record_oem_milestone_actual(
            actor=actor,
            idempotency_key=actual_key,
            po_id=po_id,
            milestone_type="OUTGOING_INSPECTION",
            payload={"actual_on": today_kst(), "version": milestone_version},
        )
    )
    waited = _observe_lock_wait(pids)
    release.set()
    cancel_thread.join(15)
    actual_thread.join(15)
    assert waited, (
        "OEM 실적이 진행 중인 PO 취소(lifecycle)를 기다리지 않았다 — T13 PO FOR SHARE 누락"
    )
    assert "error" not in cancel, cancel
    error = actual.get("error")
    assert isinstance(error, AppError), actual
    assert str(error.code) == "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE"
    assert scalar("SELECT status FROM purchase_orders WHERE id = :p", p=po_id) == "CANCELLED"
    assert _changes(po_id) == 1


# ── 같은 키 더블클릭·최초 계획 경합 (J-01·J-10) ─────────────────────────────────────────


def test_a_double_click_oem_rollover_writes_one_history_row_with_one_change_id() -> None:
    """J-01·J-14(R-19 승계) — 같은 Idempotency-Key OEM 롤오버 6스레드 동시 → 이력 PLAN_CHANGED 1행·아웃박스 1건·전 스레드 같은 change.id"""
    actor = make_user(RoleCode.TRADE)
    po_id = raw_po(po_kind="OEM_PRODUCTION")
    _plan(actor, po_id, "RAW_MATERIAL_READY", {"planned_on": today_kst()})
    version = int(scalar("SELECT version FROM milestones WHERE po_id = :p", p=po_id))
    moved = today_kst() + timedelta(days=1)  # 늘 다른 날(달력 무관)
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
    assert {o.value[1]["change"]["change_kind"] for o in outcomes} == {"PLAN_CHANGED"}
    assert _changes(po_id) == 2  # PLAN_SET 1 + PLAN_CHANGED 1
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'shipments.milestone.changed'"
            " AND aggregate_type = 'purchase_orders' AND aggregate_id = :p",
            p=po_id,
        )
        == 2
    )


def test_two_first_oem_plans_on_the_same_type_leave_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J-10(T13) — PO `FOR SHARE`는 공유 잠금이라 최초 계획 2건(다른 키)이 함께 들어온다. 두 스레드가 **모두 '행 없음'을 본 뒤**(Barrier)에야
    INSERT하게 해 번역 경로를 결정적으로 밟는다: (PO, 종류) 부분 유니크 위반이 둘째를 정확히 409 DUPLICATE_TYPE으로(500·VERSION_CONFLICT 아님).
    행 1·이력 1"""
    actor = make_user(RoleCode.TRADE)
    original = shipments_service.find_milestone
    for _ in range(ROUNDS):
        po_id = raw_po(po_kind="OEM_PRODUCTION")
        barrier = threading.Barrier(2, timeout=10)

        def both_see_none(
            session: Any,
            owner: Any,
            milestone_type: str,
            *,
            for_update: bool,
            barrier: threading.Barrier = barrier,
        ) -> Any:
            found = original(session, owner, milestone_type, for_update=for_update)
            assert found is None
            barrier.wait()  # 두 스레드가 모두 '없음'을 본 뒤 INSERT로 간다
            return found

        monkeypatch.setattr(shipments_service, "find_milestone", both_see_none)
        outcomes = run_concurrently(
            lambda i, po_id=po_id: _plan(
                actor, po_id, "FILLING", {"planned_on": today_kst() + timedelta(days=10 + i)}
            ),
            workers=2,
        )
        monkeypatch.setattr(shipments_service, "find_milestone", original)
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1, [repr(o.error) for o in outcomes]
        assert _code(next(o for o in outcomes if not o.ok)) == "SHIPMENTS.MILESTONE.DUPLICATE_TYPE"
        assert scalar("SELECT count(*) FROM milestones WHERE po_id = :p", p=po_id) == 1
        assert _changes(po_id) == 1


def test_two_admins_adding_the_same_set_type_leave_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """J — 같은 품목군·같은 종류 동시 추가 2건(다른 키): 두 스레드가 모두 peek에서 '없음'을 본 뒤(Barrier) INSERT하게 해 경합 경로를 결정적으로
    밟는다 — 부분 유니크 번역이 둘째를 409 DUPLICATE_TYPE으로(500 0), 그 409에도 peek 경로와 **같은 문구·detail**(적대 검토 반영 ⑥). 행 1"""
    admin = make_user(RoleCode.ADMIN)
    original = shipments_service.find_profile_milestone_type
    for _ in range(ROUNDS):
        profile = create_item_profile(unique("PRF"))
        barrier = threading.Barrier(2, timeout=10)

        def both_see_none(
            session: Any, profile_id: int, milestone_type: str, barrier: threading.Barrier = barrier
        ) -> Any:
            found = original(session, profile_id, milestone_type)
            assert found is None
            barrier.wait()
            return found

        monkeypatch.setattr(shipments_service, "find_profile_milestone_type", both_see_none)
        outcomes = run_concurrently(
            lambda _i, profile=profile: milestone_set_flow.add_profile_milestone_type(
                actor=admin, idempotency_key=unique("cs"), profile_id=profile, milestone_type="ETA"
            ),
            workers=2,
        )
        monkeypatch.setattr(shipments_service, "find_profile_milestone_type", original)
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1
        failed = next(o for o in outcomes if not o.ok)
        assert _code(failed) == "SHIPMENTS.MILESTONE.DUPLICATE_TYPE"
        assert isinstance(failed.error, AppError)
        assert failed.error.detail == {
            "milestone_type": "이 품목군의 마일스톤 세트에 이미 있는 종류입니다."
        }
        assert failed.error.message.startswith("같은 종류가 이미 있습니다")
        assert (
            scalar(
                "SELECT count(*) FROM item_profile_milestone_types"
                " WHERE profile_id = :p AND deleted_at IS NULL",
                p=profile,
            )
            == 1
        )
