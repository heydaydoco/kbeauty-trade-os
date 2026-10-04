"""H. 동시 20명 벌크 경합 정합 (S3-1 PR-15a / DESIGN §20 H "동시 20명 벌크 경합 정합" / design-D D6 / ADR-0066).

★ 순차 실행은 증거가 아니다 — 실제 스레드 20개를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 서로 다른 사용자·서로 다른 벌크 키로
**같은 인테이크·SO 집합을 서로 다른 순서로** 벌크 확정한다. 기대: 대상마다 SO 생성·확정이 **정확히 1회**, 나머지는 CONFLICT(또는 SKIPPED), 중복 이벤트 0,
교착(40P01) 0, 예상 못 한 실패 0, **리포트 = 실제 DB 상태**. 커밋을 동반하므로 병렬(xdist)로 돌리지 않는다.
"""

from __future__ import annotations

import random
import threading
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.identity.models import RoleCode
from app.modules.order_board import bulk as bulk_module
from app.modules.order_board import service as board_service
from app.modules.order_board.constants import BoardStage, BulkAction, BulkOutcome, CardKind
from app.modules.order_board.schemas import BoardFilter
from app.modules.trade_chain import confirm as so_confirm
from tests.factories.approvals import count
from tests.factories.board import actor, board_user
from tests.factories.confirm import ready_so, so_row, so_version
from tests.factories.intake import land, rows, world
from tests.support.concurrency import run_concurrently

pytestmark = [pytest.mark.group_h, pytest.mark.concurrency]

WORKERS = 20
INTAKES = 6
SOS = 6

#: 경합에서 허용되는 패자 코드 — 버전·상태 경합과 잠금 대기 초과뿐(교착·500·미분류는 실패).
LOSER_CODES = {
    "COMMON.CONCURRENCY.VERSION_CONFLICT",
    "ORDER_INTAKE.STATE.NOT_PENDING",
    "TRADE_DOCS.TRANSITION.NOT_ALLOWED",
    "COMMON.CONCURRENCY.LOCK_BUSY",
}


@pytest.mark.golden
def test_twenty_users_bulk_confirming_overlapping_sets_in_different_orders_confirm_each_target_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GC-F3 — 20명이 겹치는 인테이크 6·SO 6을 서로 다른 순서로 동시에 벌크 확정 → 대상마다 OK 정확히 1건(SO 생성 6·확정 6)·나머지 CONFLICT·FAILED 0·교착 0 ·
    상태 전이 이벤트·이력·확정 증거가 대상마다 1개 · 리포트의 OK 건 = DB의 확정 건(수주 id·version 일치)"""
    w = world()
    intakes = [land(w) for _ in range(INTAKES)]
    sos = [ready_so() for _ in range(SOS)]
    intake_targets = [bulk_module.Target(CardKind.INTAKE, i["id"], i["version"]) for i in intakes]
    so_targets = [bulk_module.Target(CardKind.SO, so["id"], so_version(so["id"])) for so in sos]
    actors = [actor(board_user(RoleCode.TRADE), RoleCode.TRADE) for _ in range(WORKERS)]
    so_before = count("sales_orders")

    sqlstates: list[str] = []
    lock = threading.Lock()
    original = bulk_module.classify_exception

    def spy(target: Any, exc: Exception) -> Any:
        if isinstance(exc, DBAPIError):
            with lock:
                sqlstates.append(str(getattr(exc.orig, "sqlstate", "?")))
        return original(target, exc)

    monkeypatch.setattr(bulk_module, "classify_exception", spy)

    def worker(index: int) -> list[dict[str, Any]]:
        rng = random.Random(index)
        mine_i = intake_targets[:]
        mine_s = so_targets[:]
        rng.shuffle(mine_i)
        rng.shuffle(mine_s)
        steps = [(BulkAction.CONFIRM_INTAKE, mine_i), (BulkAction.CONFIRM_SO, mine_s)]
        if index % 2:
            steps.reverse()
        return [
            bulk_module.run_bulk(
                actor=actors[index],
                idempotency_key=f"race-{index}-{action.value}",  # 같은 키는 같은 요청에만
                action=action,
                targets=targets,
            )
            for action, targets in steps
        ]

    outcomes = run_concurrently(worker, workers=WORKERS, timeout=300)
    errors = [o.error for o in outcomes if not o.ok]
    assert errors == []
    assert "40P01" not in sqlstates  # 교착 0

    by_target: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for outcome in outcomes:
        for report in outcome.value:
            assert (
                report["ok_count"] + report["skipped_count"] + report["fail_count"]
                == report["total"]
            )
            for item in report["results"]:
                by_target.setdefault((item["kind"], item["id"]), []).append(item)

    assert len(by_target) == INTAKES + SOS
    for (kind, target_id), items in by_target.items():
        assert len(items) == WORKERS, (kind, target_id)
        winners = [i for i in items if i["outcome"] == BulkOutcome.OK.value]
        losers = [i for i in items if i["outcome"] != BulkOutcome.OK.value]
        assert len(winners) == 1, (kind, target_id, [i["outcome"] for i in items])
        for loser in losers:
            assert loser["outcome"] in (BulkOutcome.CONFLICT.value, BulkOutcome.SKIPPED.value)
            assert loser["code"] in LOSER_CODES, loser
        winner = winners[0]
        if kind == "INTAKE":
            (row,) = rows(
                "SELECT status, sales_order_id, version FROM order_intakes WHERE id = :i",
                i=target_id,
            )
            assert row.status == "CONFIRMED" and row.sales_order_id == winner["sales_order_id"]
            assert row.version == winner["version"]
            assert so_row(row.sales_order_id)["doc_number"] == winner["doc_number"]
            assert (
                count(
                    "events",
                    "event_type = 'order_intakes.order_intake.status_changed' AND aggregate_id = :i",
                    i=target_id,
                )
                == 1
            )
            assert (
                count(
                    "events",
                    "event_type = 'sales_orders.sales_order.created' AND aggregate_id = :i",
                    i=row.sales_order_id,
                )
                == 1
            )
        else:
            row_so = so_row(target_id)
            assert row_so["status"] == "CONFIRMED" and row_so["version"] == winner["version"]
            assert (
                count(
                    "sales_order_status_log",
                    "sales_order_id = :i AND to_status = 'CONFIRMED'",
                    i=target_id,
                )
                == 1
            )
            assert (
                count(
                    "gate_evaluations",
                    "subject_type = 'SALES_ORDER' AND subject_id = :i AND outcome = 'CONFIRMED'",
                    i=target_id,
                )
                == 1
            )
            assert (
                count(
                    "events",
                    "event_type = 'sales_orders.sales_order.status_changed' AND aggregate_id = :i"
                    " AND payload->>'to_status' = 'CONFIRMED'",
                    i=target_id,
                )
                == 1
            )

    assert count("sales_orders") == so_before + INTAKES  # 인테이크마다 SO 정확히 1건
    assert count("order_intakes", "status = 'CONFIRMED' AND sales_order_id IS NOT NULL") == INTAKES


# ── 보드 읽기 스냅샷(REPEATABLE READ, READ ONLY) ─────────────────────────────────


def _assert_consistent(board: dict[str, Any]) -> list[tuple[str, int]]:
    """한 보드 응답의 정합 — (kind,id) 중복 0, 열마다 total ≥ 카드 수·has_more = (total > 카드 수)."""
    keys = [(item["kind"], item["id"]) for col in board["columns"] for item in col["items"]]
    assert len(keys) == len(set(keys)), keys
    for col in board["columns"]:
        assert col["total"] >= len(col["items"]), col["stage"]
        assert col["has_more"] is (col["total"] > len(col["items"])), col["stage"]
    return keys


def test_a_confirmation_committed_between_the_board_queries_never_shows_a_card_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """결정적 인터리브 — 보드가 '수주 접수' 열을 읽은 **직후** 다른 연결에서 그 SO가 실제 확정 통로로 확정·커밋돼도, 보드 7쿼리는 한 스냅샷이라
    '수주 확정' 열에 다시 나오지 않고 열 건수와 카드가 어긋나지 않는다(READ COMMITTED였다면 같은 카드가 두 열에 실리고 확정 열 total 0 < 카드 1)"""
    so = ready_so()
    trade = actor(board_user(RoleCode.TRADE), RoleCode.TRADE)
    original = board_service._so_cards
    fired: list[BaseException | None] = []

    def confirm_elsewhere(session: Any, f: Any, stage: BoardStage, **kwargs: Any) -> Any:
        cards = original(session, f, stage, **kwargs)
        if stage is BoardStage.SO_RECEIVED and not fired:

            def run() -> None:
                try:
                    so_confirm.confirm_sales_order(
                        actor=trade,
                        idempotency_key="interleave",
                        so_id=so["id"],
                        version=so_version(so["id"]),
                    )
                    fired.append(None)
                except BaseException as exc:  # 결과로 판정한다
                    fired.append(exc)

            # 새 스레드 = 새 연결·새 트랜잭션(보드 트랜잭션에 합류하지 않는다)
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(60)
        return cards

    monkeypatch.setattr(board_service, "_so_cards", confirm_elsewhere)
    board = board_service.get_order_board(BoardFilter())
    assert fired == [None]  # 보드 쿼리 사이에 확정이 실제로 커밋됐다
    assert so_row(so["id"])["status"] == "CONFIRMED"
    keys = _assert_consistent(board)
    assert keys.count(("SO", so["id"])) == 1
    by_stage = {col["stage"]: col for col in board["columns"]}
    assert [i["id"] for i in by_stage["SO_RECEIVED"]["items"]] == [so["id"]]
    assert by_stage["SO_CONFIRMED"]["items"] == [] and by_stage["SO_CONFIRMED"]["total"] == 0
    after = board_service.get_order_board(
        BoardFilter()
    )  # 다음 읽기는 새 스냅샷 — 확정 열로 옮겨 있다
    assert [i["id"] for i in after["columns"][3]["items"]] == [so["id"]]


def test_polling_the_board_while_bulk_confirmations_land_never_duplicates_a_card() -> None:
    """H — 보드를 계속 읽는 동안 다른 사용자 4명이 벌크 확정을 건건이 커밋해도 어떤 응답에도 (kind,id) 중복이 없고 열마다 total ≥ 카드 수다.
    끝난 뒤 보드는 12건 전부를 확정 열에 보인다"""
    confirmers = 4
    sos = [ready_so() for _ in range(12)]
    targets = [bulk_module.Target(CardKind.SO, so["id"], so_version(so["id"])) for so in sos]
    actors = [actor(board_user(RoleCode.TRADE), RoleCode.TRADE) for _ in range(confirmers)]
    done = threading.Event()
    finished = {"n": 0}
    lock = threading.Lock()

    def worker(index: int) -> Any:
        if index == confirmers:  # 읽는 사람
            boards: list[dict[str, Any]] = []
            while (not done.is_set() or len(boards) < 3) and len(boards) < 2_000:
                boards.append(board_service.get_order_board(BoardFilter()))
            return boards
        try:
            return [
                bulk_module.run_bulk(
                    actor=actors[index],
                    idempotency_key=f"poll-{index}-{t.id}",
                    action=BulkAction.CONFIRM_SO,
                    targets=[t],
                )
                for t in targets[index::confirmers]
            ]
        finally:
            with lock:
                finished["n"] += 1
                if finished["n"] == confirmers:
                    done.set()

    outcomes = run_concurrently(worker, workers=confirmers + 1, timeout=300)
    assert [o.error for o in outcomes if not o.ok] == []
    for outcome in outcomes[:confirmers]:
        assert all(report["ok_count"] == 1 for report in outcome.value)
    boards = outcomes[confirmers].value
    assert len(boards) >= 3
    for board in boards:
        _assert_consistent(board)
    final = board_service.get_order_board(BoardFilter())
    _assert_consistent(final)
    assert {i["id"] for i in final["columns"][3]["items"]} == {so["id"] for so in sos}
