"""J. 안전 계약 — 아웃박스 디스패처 (§17.1·§17.6·§20 J / 부채 #11 소비).

§20 J가 요구하는 "아웃박스: 커밋 실패 시 발송 0 · 발송 실패 재시도"와
"동일 임포트 재실행 변화 0"의 디스패처 몫이 여기 있다.

★ 클레임 경합은 **실제 동시 실행**으로 본다(GC-F1 계보 — 순차 실행은 경합을
  만들지 못한다). SKIP LOCKED가 도는지는 두 스레드가 같은 순간 같은 행을
  노려야 드러난다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.identity.models import RoleCode
from app.modules.notifications import dispatcher
from app.modules.notifications.dispatcher import MAX_ATTEMPTS
from app.modules.outbox import service as outbox
from app.modules.outbox.models import Event
from app.modules.worklist.models import Alert, AlertRule
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_user

pytestmark = pytest.mark.group_j

EVENT = "worklist.task.created"


@pytest.fixture
def subscriber() -> Iterator[int]:
    """이 이벤트를 구독하는 규칙 + 명시 수신자 1인."""
    user_id = create_user("subscriber@example.com", roles=(RoleCode.TRADE,))
    with unit_of_work() as uow:
        uow.session.add(
            AlertRule(
                code="TASK-CREATED",
                name_ko="태스크가 생성됐습니다",
                event_type=EVENT,
                recipient_user_id=user_id,
            )
        )
    yield user_id


def _publish(event_type: str = EVENT, **payload: object) -> int:
    with unit_of_work() as uow:
        event = outbox.publish(
            uow.session,
            event_type=event_type,
            aggregate_type="tasks",
            aggregate_id=1,
            payload=dict(payload),
        )
        uow.session.flush()
        return event.id


def _event(event_id: int) -> Event:
    with unit_of_work() as uow:
        row = uow.session.get(Event, event_id)
        assert row is not None
        uow.session.expunge(row)
        return row


def _alert_count() -> int:
    with unit_of_work() as uow:
        total: int = uow.session.execute(select(func.count()).select_from(Alert)).scalar_one()
        return total


# ── 소비의 기본 계약 ────────────────────────────────────────────────────────


def test_a_matching_event_becomes_an_alert_and_is_marked_published(subscriber: int) -> None:
    event_id = _publish()
    counts = dispatcher.dispatch_pending()
    assert counts["published"] == 1
    assert counts["alerts"] == 1
    assert _event(event_id).published_at is not None
    assert _alert_count() == 1


def test_an_unmatched_event_is_still_published(subscriber: int) -> None:
    """구독자 없는 이벤트도 발행 완료로 적는다 — 안 그러면 대기열이 안 줄어든다"""
    event_id = _publish(event_type="catalog.brand.created")
    counts = dispatcher.dispatch_pending()
    assert counts["published"] == 1
    assert counts["alerts"] == 0
    assert _event(event_id).published_at is not None


def test_running_twice_changes_nothing(subscriber: int) -> None:
    """§20 J "재실행 변화 0" — 두 번째 실행은 집을 것이 없다"""
    _publish()
    dispatcher.dispatch_pending()
    second = dispatcher.dispatch_pending()
    assert second == {"scanned": 0, "published": 0, "skipped": 0, "failed": 0, "alerts": 0}
    assert _alert_count() == 1


def test_the_assignee_in_the_payload_wins_over_nothing() -> None:
    """수신자가 빈 규칙 + payload 담당자 → 담당자에게만(DoD 전사 폭포 0건)"""
    assignee = create_user("payload-assignee@example.com", roles=(RoleCode.TRADE,))
    create_user("bystander-admin@example.com", roles=(RoleCode.ADMIN,))
    with unit_of_work() as uow:
        uow.session.add(AlertRule(code="EMPTY", name_ko="담당자 알림", event_type=EVENT))
    _publish(assignee_id=assignee)

    dispatcher.dispatch_pending()
    with unit_of_work() as uow:
        recipients = set(uow.session.execute(select(Alert.recipient_user_id)).scalars())
    assert recipients == {assignee}


# ── 실패·재시도 (§17.6) ─────────────────────────────────────────────────────


def _break_fan_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("채널이 응답하지 않습니다")

    monkeypatch.setattr(dispatcher, "_fan_out", _boom)


def test_a_failure_is_recorded_and_scheduled_for_retry(
    subscriber: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = _publish()
    _break_fan_out(monkeypatch)

    counts = dispatcher.dispatch_pending()
    assert counts["failed"] == 1

    row = _event(event_id)
    assert row.published_at is None
    assert row.attempts == 1
    assert row.last_error is not None and "RuntimeError" in row.last_error
    assert row.next_retry_at is not None


def test_a_backed_off_event_is_not_picked_up_again(
    subscriber: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """백오프 중인 건은 다음 주기에 집히지 않는다 — 실패 건이 앞줄을 막지 않는다"""
    _publish()
    _break_fan_out(monkeypatch)
    dispatcher.dispatch_pending()
    assert dispatcher.dispatch_pending()["scanned"] == 0


def test_one_failure_does_not_block_the_others(
    subscriber: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 건이 실패해도 나머지는 나간다(§17.6 청크) — 건별 독립 트랜잭션"""
    poisoned = _publish()
    healthy = _publish()

    original = dispatcher._fan_out

    def _selective(session: object, event: Event) -> int:
        if event.id == poisoned:
            raise RuntimeError("이 건만 실패")
        return original(session, event)  # type: ignore[arg-type]

    monkeypatch.setattr(dispatcher, "_fan_out", _selective)

    counts = dispatcher.dispatch_pending()
    assert counts["failed"] == 1
    assert counts["published"] == 1
    assert _event(healthy).published_at is not None
    assert _event(poisoned).published_at is None


def test_reaching_the_attempt_cap_alerts_an_admin_and_leaves_the_queue(
    subscriber: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """포이즌 이벤트는 조용히 남지 않는다 — 상한에서 관리자 알림 1회(§17.6)"""
    admin = create_user("poison-admin@example.com", roles=(RoleCode.ADMIN,))
    event_id = _publish()
    _break_fan_out(monkeypatch)

    for attempt in range(MAX_ATTEMPTS):
        # ★ 기준 시각을 매 회 전진시킨다. 같은 시각으로 반복하면 첫 실패가 찍은
        #   next_retry_at에 막혀 두 번째부터 후보에서 빠지고, 시도가 소진되지
        #   않는다(그 자체가 백오프가 도는 증거지만 여기서 볼 것은 상한이다).
        dispatcher.dispatch_pending(now=utcnow() + timedelta(days=attempt + 1))

    row = _event(event_id)
    assert row.attempts == MAX_ATTEMPTS
    assert row.published_at is None  # 보내지 않은 것을 보냈다고 적지 않는다

    with unit_of_work() as uow:
        poison = uow.session.execute(
            select(Alert).where(Alert.dedup_key == f"outbox.poison:events:{event_id}:{admin}")
        ).scalar_one()
    assert poison.severity == "CRITICAL"

    # 상한을 넘긴 건은 대기열에서 빠진다 — 스핀하지 않는다.
    assert dispatcher.dispatch_pending(now=utcnow() + timedelta(days=2))["scanned"] == 0


def test_the_poison_alert_is_not_repeated(subscriber: int, monkeypatch: pytest.MonkeyPatch) -> None:
    create_user("poison-admin-2@example.com", roles=(RoleCode.ADMIN,))
    event_id = _publish()
    _break_fan_out(monkeypatch)
    for attempt in range(MAX_ATTEMPTS + 2):
        dispatcher.dispatch_pending(now=utcnow() + timedelta(days=attempt + 1))

    with unit_of_work() as uow:
        total: int = uow.session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.entity_type == "events", Alert.entity_id == event_id)
        ).scalar_one()
    assert total == 1


# ── 경합 (실제 동시 실행) ───────────────────────────────────────────────────


def test_two_dispatchers_do_not_double_send(subscriber: int) -> None:
    """더블 디스패치 — 알림 1건, attempts 증가 0(SKIP LOCKED 클레임)"""
    event_id = _publish()

    outcomes = run_concurrently(lambda _i: dispatcher.dispatch_pending(), workers=2)
    assert all(outcome.ok for outcome in outcomes), [o.error for o in outcomes]

    published = sum(outcome.value["published"] for outcome in outcomes)
    assert published == 1
    assert _alert_count() == 1
    assert _event(event_id).attempts == 0


def test_backoff_grows_with_attempts() -> None:
    """백오프는 시도마다 길어진다 — 마지막 값에서 멈춘다(무한 증가 금지)"""
    steps = [dispatcher.backoff_for(n).total_seconds() for n in range(1, MAX_ATTEMPTS + 1)]
    assert steps == sorted(steps)
    assert steps[0] < steps[-1]
    assert steps[-1] == dispatcher.BACKOFF_SECONDS[-1]
