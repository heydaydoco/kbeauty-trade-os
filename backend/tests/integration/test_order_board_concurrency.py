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
from app.modules.order_board.constants import BulkAction, BulkOutcome, CardKind
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


def test_twenty_users_bulk_confirming_overlapping_sets_in_different_orders_confirm_each_target_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """20명이 겹치는 인테이크 6·SO 6을 서로 다른 순서로 동시에 벌크 확정 → 대상마다 OK 정확히 1건(SO 생성 6·확정 6)·나머지 CONFLICT·FAILED 0·교착 0 ·
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
                idempotency_key=f"race-{index}",
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
