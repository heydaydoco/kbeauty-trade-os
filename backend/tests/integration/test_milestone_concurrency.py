"""J. 마일스톤·통관 동시성 — 실적 기록 vs 선적 취소·통관 추가 vs 취소·같은 키 더블클릭·최초 계획 경합·잠금 순서 계측
(S3-2 PR-4a / design-C C2·C4·C5·C14 J-01·J-07·J-10·J-14 / ADR-0078 / design-integrated §9 R-01·R-08·R-16·R-19).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(각 스레드 자기 세션).
  불변식: 취소된 선적에 살아 있는 실적(ETD·B/L·ETA)·통관 기록 0 — 판정과 취소가 같은 선적 `FOR UPDATE`로 직렬화된다. 500·교착 0.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event

from app.core.db.session import engine
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.trade_chain import customs_flow, milestone_flow, shipment_flow
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.approvals import make_user
from tests.factories.shipments import confirmed_so, create_body, scalar
from tests.factories.trade import create_supplier, unique
from tests.support.concurrency import Outcome, run_concurrently
from tests.support.kst import pin_today_kst

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """KST 자정 경계 고정(적대 검토 반영 ⑩) — 시험마다 base 날짜를 한 번 잡아
    앱 import 지점(마일스톤·통관·선적 흐름·보드)과 이 시험 모듈의 `today_kst`를 같은 날로 맞춘다."""
    pin_today_kst(monkeypatch, sys.modules[__name__])


ROUNDS = 5


def _released_shipment(actor: AuthenticatedUser) -> dict[str, Any]:
    so = confirmed_so((10,))
    _, body = shipment_flow.create_shipment_from_sales_order(
        actor=actor,
        idempotency_key=unique("sh"),
        so_id=so["id"],
        payload=create_body([(so["line_ids"][0], 3)]),
    )
    _, body = shipment_flow.release_shipment_order(
        actor=actor, idempotency_key=unique("rl"), shipment_id=body["id"], version=body["version"]
    )
    return body


def _cancel(actor: AuthenticatedUser, shipment_id: int) -> tuple[int, dict[str, Any]]:
    version = int(scalar("SELECT version FROM shipments WHERE id = :i", i=shipment_id))
    return shipment_flow.transition_shipment(
        actor=actor,
        idempotency_key=unique("cx"),
        shipment_id=shipment_id,
        to="CANCELLED",
        version=version,
        reason="동시성",
    )


def _no_db_errors(outcomes: list[Outcome]) -> None:
    for outcome in outcomes:
        assert outcome.ok or isinstance(outcome.error, AppError), repr(outcome.error)


def _code(outcome: Outcome) -> str:
    assert isinstance(outcome.error, AppError)
    return str(outcome.error.code)


def test_an_actual_and_a_cancel_never_both_succeed() -> None:
    """R-01 동시 — 출고지시 선적에 ETD 실적 기록 vs 선적 취소: 매 라운드 정확히 하나만 성공하고, 실적이 먼저면 취소 409 ACTUAL_RECORDED,
    취소가 먼저면 실적 409 OWNER_NOT_ACTIVE. 취소된 선적에 살아 있는 실적 0(거짓 상태 0)·500·교착 0"""
    actor = make_user(RoleCode.TRADE)
    seen: set[str] = set()
    for _ in range(ROUNDS):
        shipment = _released_shipment(actor)
        sid = shipment["id"]

        def work(index: int, sid: int = sid) -> object:
            if index == 0:
                return milestone_flow.record_milestone_actual(
                    actor=actor,
                    idempotency_key=unique("act"),
                    shipment_id=sid,
                    milestone_type="ETD",
                    payload={"actual_on": today_kst()},
                )
            return _cancel(actor, sid)

        outcomes = run_concurrently(work, workers=2)
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1, [repr(o.error) for o in outcomes]
        failed = next(o for o in outcomes if not o.ok)
        expected = {
            0: "SHIPMENTS.MILESTONE.OWNER_NOT_ACTIVE",
            1: "SHIPMENTS.SHIPMENT.ACTUAL_RECORDED",
        }[failed.index]
        assert _code(failed) == expected
        seen.add(expected)
        status = scalar("SELECT status FROM shipments WHERE id = :i", i=sid)
        live_actual = scalar(
            "SELECT count(*) FROM milestones WHERE shipment_id = :i AND actual_on IS NOT NULL",
            i=sid,
        )
        assert (status == "CANCELLED") == (live_actual == 0), (status, live_actual)
    assert seen  # 적어도 한 방향은 관측(두 방향 모두 정상 경로)


def test_a_customs_record_and_a_cancel_never_both_succeed() -> None:
    """R-16 동시 — 통관 기록 추가 vs 선적 취소: 정확히 하나만 성공(기록 먼저 → 취소 409 CUSTOMS_RECORD_ALIVE, 취소 먼저 → 추가 409 NOT_ACTIVE),
    취소된 선적에 살아 있는 통관 기록 0"""
    actor = make_user(RoleCode.TRADE)
    for round_no in range(ROUNDS):
        sid = _released_shipment(actor)["id"]

        def work(index: int, sid: int = sid, round_no: int = round_no) -> object:
            if index == 0:
                return customs_flow.create_customs_record(
                    actor=actor,
                    idempotency_key=unique("cr"),
                    shipment_id=sid,
                    payload={
                        "declaration_kind": "EXPORT",
                        "declaration_no": f"RACE-{sid}-{round_no}",
                        "declared_on": today_kst(),
                    },
                )
            return _cancel(actor, sid)

        outcomes = run_concurrently(work, workers=2)
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1
        failed = next(o for o in outcomes if not o.ok)
        assert (
            _code(failed)
            == {
                0: "SHIPMENTS.SHIPMENT.NOT_ACTIVE",
                1: "SHIPMENTS.SHIPMENT.CUSTOMS_RECORD_ALIVE",
            }[failed.index]
        )
        status = scalar("SELECT status FROM shipments WHERE id = :i", i=sid)
        live = scalar(
            "SELECT count(*) FROM customs_records WHERE shipment_id = :i AND deleted_at IS NULL",
            i=sid,
        )
        assert (status == "CANCELLED") == (live == 0)


def test_a_double_click_rollover_writes_one_history_row_with_one_change_id() -> None:
    """J-01·J-14(R-19) — 같은 Idempotency-Key 롤오버 6스레드 동시 → 이력 PLAN_CHANGED 1행·아웃박스 1건·전 스레드 같은 change.id"""
    actor = make_user(RoleCode.TRADE)
    sid = _released_shipment(actor)["id"]
    milestone_flow.record_milestone_plan(
        actor=actor,
        idempotency_key=unique("p0"),
        shipment_id=sid,
        milestone_type="ETA",
        payload={"planned_on": today_kst()},
    )
    version = int(scalar("SELECT version FROM milestones WHERE shipment_id = :i", i=sid))
    key = unique("dbl")
    payload = {"planned_on": today_kst().replace(day=1), "reason": "롤오버", "version": version}
    if payload["planned_on"] == today_kst():
        payload["planned_on"] = today_kst().replace(day=2)
    outcomes = run_concurrently(
        lambda _i: milestone_flow.record_milestone_plan(
            actor=actor, idempotency_key=key, shipment_id=sid, milestone_type="ETA", payload=payload
        ),
        workers=6,
    )
    _no_db_errors(outcomes)
    assert all(o.ok for o in outcomes), [repr(o.error) for o in outcomes]
    ids = {o.value[1]["change"]["id"] for o in outcomes}
    assert len(ids) == 1
    assert (
        scalar(
            "SELECT count(*) FROM milestone_changes mc JOIN milestones m ON m.id = mc.milestone_id"
            " WHERE m.shipment_id = :i AND mc.change_kind = 'PLAN_CHANGED'",
            i=sid,
        )
        == 1
    )
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'shipments.milestone.changed'"
            " AND aggregate_id = :i",
            i=sid,
        )
        == 2
    )


def test_concurrent_notices_never_exceed_the_per_change_limit() -> None:
    """적대 검토 반영 ⑧ — 변경 1건의 통보가 상한-2건일 때 5스레드 동시 통보 → 정확히 2건 성공·3건 422 NOTICE_LIMIT_REACHED·
    연결 행 = 상한(소유 마일스톤 행 잠금 아래에서 세므로 경합으로 넘치지 않는다)·500·교착 0"""
    actor = make_user(RoleCode.TRADE)
    sid = _released_shipment(actor)["id"]
    _, body = milestone_flow.record_milestone_plan(
        actor=actor,
        idempotency_key=unique("np"),
        shipment_id=sid,
        milestone_type="ETD",
        payload={"planned_on": today_kst()},
    )
    change_id = int(body["change"]["id"])
    limit = milestone_flow.NOTICE_LIMIT_PER_CHANGE

    def notice(_index: int) -> object:
        return milestone_flow.record_milestone_notice(
            actor=actor,
            idempotency_key=unique("nt"),
            shipment_id=sid,
            change_id=change_id,
            payload={"occurred_on": today_kst(), "summary": "포워더 통보"},
        )

    for index in range(limit - 2):
        notice(index)
    outcomes = run_concurrently(notice, workers=5)
    _no_db_errors(outcomes)
    assert sum(o.ok for o in outcomes) == 2, [repr(o.error) for o in outcomes]
    assert {_code(o) for o in outcomes if not o.ok} == {"SHIPMENTS.MILESTONE.NOTICE_LIMIT_REACHED"}
    assert (
        scalar("SELECT count(*) FROM milestone_change_notices WHERE change_id = :c", c=change_id)
        == limit
    )


def test_two_first_plans_on_the_same_type_serialize_into_one_row() -> None:
    """J-10 — 같은 종류의 최초 계획 2건(다른 키)이 동시에 오면 선적 `FOR UPDATE`가 직렬화한다: 하나는 PLAN_SET, 다른 하나는 행이 생긴 뒤라
    version 누락 = 409(겹친 편집) — 행 1개·이력 1행, DUPLICATE_TYPE·500 0"""
    actor = make_user(RoleCode.TRADE)
    for _ in range(ROUNDS):
        sid = _released_shipment(actor)["id"]
        outcomes = run_concurrently(
            lambda i, sid=sid: milestone_flow.record_milestone_plan(
                actor=actor,
                idempotency_key=unique("fp"),
                shipment_id=sid,
                milestone_type="ETD",
                payload={"planned_on": today_kst().replace(day=10 + i)},
            ),
            workers=2,
        )
        _no_db_errors(outcomes)
        assert sum(o.ok for o in outcomes) == 1
        assert _code(next(o for o in outcomes if not o.ok)) == "COMMON.CONCURRENCY.VERSION_CONFLICT"
        assert scalar("SELECT count(*) FROM milestones WHERE shipment_id = :i", i=sid) == 1


# ── 잠금 순서(LOCK_ORDER) 계측 — J-07(T6·T7·T8·T9·M4) ─────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "sales_orders": "sales_orders",
    "purchase_orders": "purchase_orders",
    "shipments": "shipments",
    "shipment_parties": "shipment_children",
    "milestones": "shipment_children",
    "customs_records": "shipment_children",
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


def test_every_milestone_and_customs_operation_takes_locks_in_the_documented_order() -> None:
    """J-07 — 계획·실적·초안 = 멱등→선적→하위 행(마일스톤) / 통보 = 멱등→거래처→선적(SHARE)→하위 행(상한 판정) / 통관 추가 = 멱등→거래처(관세사)→선적 /
    통관 정정(관세사 변경) = 거래처→선적→하위 행 / 통관 삭제 = 선적→하위 행 / 취소(가드 포함) = 멱등→SO→선적 — 개정 LOCK_ORDER 부분수열"""
    actor = make_user(RoleCode.TRADE)
    sid = _released_shipment(actor)["id"]
    holder: dict[str, Any] = {}

    def plan() -> None:
        holder["plan"] = milestone_flow.record_milestone_plan(
            actor=actor,
            idempotency_key=unique("lp"),
            shipment_id=sid,
            milestone_type="ETD",
            payload={"planned_on": today_kst()},
        )[1]

    seen = _first_lock_sequence(plan)
    assert seen == ["idempotency_keys", "shipments", "shipment_children"], seen
    _assert_follows_lock_order(seen)
    version = int(scalar("SELECT version FROM milestones WHERE shipment_id = :i", i=sid))

    def actual() -> None:
        milestone_flow.record_milestone_actual(
            actor=actor,
            idempotency_key=unique("la"),
            shipment_id=sid,
            milestone_type="ETD",
            payload={"actual_on": today_kst(), "version": version},
        )

    seen = _first_lock_sequence(actual)
    assert seen == ["idempotency_keys", "shipments", "shipment_children"], seen

    seen = _first_lock_sequence(
        lambda: milestone_flow.draft_milestone_plan(
            actor=actor, idempotency_key=unique("ld"), shipment_id=sid
        )
    )
    assert seen == ["idempotency_keys", "shipments"], seen  # 새 행 INSERT뿐(기존 행 잠금 없음)
    _assert_follows_lock_order(seen)

    forwarder = create_supplier(types=("FORWARDER",))
    change_id = int(holder["plan"]["change"]["id"])
    seen = _first_lock_sequence(
        lambda: milestone_flow.record_milestone_notice(
            actor=actor,
            idempotency_key=unique("ln"),
            shipment_id=sid,
            change_id=change_id,
            payload={
                "occurred_on": today_kst(),
                "counterpart_partner_id": forwarder,
                "summary": "포워더 통보",
            },
        )
    )
    # 통보 상한 판정 = 소유 마일스톤 행 FOR UPDATE(shipment_children — 선적 SHARE 뒤, 적대 검토 반영 ⑧)
    assert seen == ["idempotency_keys", "partners", "shipments", "shipment_children"], seen
    _assert_follows_lock_order(seen)

    broker = create_supplier(types=("CUSTOMS_BROKER",))
    other_broker = create_supplier(types=("CUSTOMS_BROKER",))

    def create_record() -> None:
        holder["record"] = customs_flow.create_customs_record(
            actor=actor,
            idempotency_key=unique("lc"),
            shipment_id=sid,
            payload={
                "declaration_kind": "EXPORT",
                "declaration_no": f"LOCK-{sid}",
                "declared_on": today_kst(),
                "customs_broker_partner_id": broker,
            },
        )[1]

    seen = _first_lock_sequence(create_record)
    assert seen == ["idempotency_keys", "partners", "shipments"], seen
    _assert_follows_lock_order(seen)
    record = holder["record"]

    def update_record() -> None:
        holder["record"] = customs_flow.update_customs_record(
            actor=actor,
            shipment_id=sid,
            record_id=record["id"],
            payload={"version": record["version"], "customs_broker_partner_id": other_broker},
        )

    seen = _first_lock_sequence(update_record)
    assert seen == ["partners", "shipments", "shipment_children"], seen
    _assert_follows_lock_order(seen)

    seen = _first_lock_sequence(
        lambda: customs_flow.delete_customs_record(
            actor=actor,
            shipment_id=sid,
            record_id=record["id"],
            payload={"version": holder["record"]["version"], "reason": "계측"},
        )
    )
    assert seen == ["shipments", "shipment_children"], seen
    _assert_follows_lock_order(seen)

    def cancel_blocked() -> None:
        with pytest.raises(AppError):
            _cancel(actor, sid)  # ETD 실적 생존 → 409(가드 판정도 선적 잠금 뒤 — 잠금 순서 그대로)

    seen = _first_lock_sequence(cancel_blocked)
    assert seen == ["idempotency_keys", "sales_orders", "shipments"], seen
    _assert_follows_lock_order(seen)
