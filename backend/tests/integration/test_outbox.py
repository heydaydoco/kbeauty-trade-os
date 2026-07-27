"""J. 안전 계약 — 아웃박스 (DESIGN.md §17.1 / §20 J "커밋 실패 시 발송 0").

PROGRESS 부채 #7이 여기서 종결된다 — events 테이블이 생겨 검증이 가능해졌다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.modules.outbox import service as outbox
from app.modules.worklist.models import Task

pytestmark = pytest.mark.group_j


def _event_count() -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM events")).scalar_one()


def test_event_is_written_in_the_business_transaction() -> None:
    """업무가 커밋되면 이벤트도 함께 커밋된다"""
    with unit_of_work() as uow:
        task = Task(title="선적 준비")
        uow.session.add(task)
        uow.session.flush()
        outbox.publish(
            uow.session,
            event_type="worklist.task.created",
            aggregate_type="tasks",
            aggregate_id=task.id,
            payload={"title": task.title},
        )

    assert _event_count() == 1


def test_rolled_back_work_publishes_nothing() -> None:
    """트랜잭션이 실패하면 발송할 이벤트도 0건이다 (§20 J)

    ★ 이게 아웃박스의 존재 이유다. 트랜잭션 안에서 슬랙·메일을 직접 불렀다면
      이 시나리오에서 **일어나지 않은 일이 이미 대외로 나간 상태**가 된다.
    """
    with pytest.raises(RuntimeError), unit_of_work() as uow:
        task = Task(title="롤백될 일")
        uow.session.add(task)
        uow.session.flush()
        outbox.publish(uow.session, event_type="worklist.task.created", payload={"x": 1})
        raise RuntimeError("업무 실패")

    assert _event_count() == 0, "롤백됐는데 발송 대기 이벤트가 남았다"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM tasks")).scalar_one() == 0


def test_new_events_are_unpublished() -> None:
    """갓 적은 이벤트는 미발송 상태다 (디스패처가 집어갈 대상)"""
    with unit_of_work() as uow:
        outbox.publish(uow.session, event_type="worklist.task.created")

    with unit_of_work() as uow:
        waiting = outbox.pending(uow.session)
        assert len(waiting) == 1
        assert waiting[0].published_at is None
        assert waiting[0].attempts == 0


def test_published_events_leave_the_queue() -> None:
    """발송 표시가 된 이벤트는 대기열에서 빠진다"""
    from app.core.time import utcnow

    with unit_of_work() as uow:
        outbox.publish(uow.session, event_type="worklist.task.created")

    with unit_of_work() as uow:
        waiting = outbox.pending(uow.session)
        waiting[0].published_at = utcnow()

    with unit_of_work() as uow:
        assert outbox.pending(uow.session) == []


def test_after_commit_hook_runs_only_after_the_transaction_closes() -> None:
    """디스패처 기동은 커밋 뒤에만 일어난다 (§17.1 "외부 호출은 커밋 후에")"""
    fired: list[int] = []

    with unit_of_work() as uow:
        outbox.publish(uow.session, event_type="worklist.task.created")
        uow.add_after_commit(lambda: fired.append(_event_count()))
        assert fired == [], "커밋 전에 디스패처가 돌았다"

    assert fired == [1], "커밋 뒤에도 디스패처가 돌지 않았다"
