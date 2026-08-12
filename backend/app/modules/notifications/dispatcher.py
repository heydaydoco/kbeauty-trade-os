"""아웃박스 디스패처 (DESIGN.md §17.1 / §17.6 / 부채 #11 소비).

S0-2부터 events는 쌓이기만 했다 — 적는 주체는 있고 보내는 주체가 없었다.
이 파일이 그 주체다.

★ **클레임=처리=표식이 한 트랜잭션이다.** 행 잠금의 수명은 트랜잭션이므로,
  "여러 건을 미리 잠가 두고 건별로 커밋"하면 첫 커밋에서 나머지 잠금이 풀려
  동시 디스패처가 같은 이벤트를 다시 집는다. 그래서 건당 1클레임이다:
  `SELECT … FOR UPDATE SKIP LOCKED` → 규칙 평가 → 알림 생성 → published_at.
  후보 id 수집만 잠금 밖에서 하고(가벼운 읽기), 실제 판단은 잠근 뒤 다시 한다.

★ **한 건의 실패가 나머지를 막지 않는다**(§17.6 청크·실패 지점 기록). 실패는
  그 건의 트랜잭션만 롤백하고, 실패 기록(attempts·last_error·next_retry_at)은
  **별도 트랜잭션**으로 남긴다 — 같은 트랜잭션에 적으면 롤백과 함께 사라져
  영원히 같은 실패를 반복한다.

★ **포이즌 이벤트는 조용히 남지 않는다**(§17.6 "실패 알림"). 시도가 상한(5회)에
  닿으면 대기열에서 빠지고 관리자 알림이 1회 나간다. 대기열 이탈을
  `published_at`으로 표시하지 않는 이유: 보내지 않은 것을 보냈다고 적는 셈이라
  events가 발행 원장으로서 거짓이 된다. 대기열 질의가 `attempts < 상한`으로
  거른다.

★ 이번 세션의 "발송"은 **alerts INSERT(내부 DB 쓰기)뿐**이다. 외부 커넥터는
  S6-3이고, 그때도 호출은 커밋 뒤여야 한다(§17.1) — 이 파일 안에서 HTTP를
  부르지 않는다는 사실을 아키텍처 테스트가 지킨다.

★ 평가 대상은 alert_rules뿐이다. webhook_subscriptions는 보낼 커넥터가 생기는
  S6-3에서 평가한다(그전에는 매칭돼도 갈 곳이 없다 — 0행 유지와 정합).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.time import utcnow
from app.modules.notifications import service as notifications
from app.modules.outbox.models import Event

#: 시도 상한 — 넘으면 대기열에서 빠지고 관리자 알림 1회.
MAX_ATTEMPTS = 5

#: 지수 백오프(초). 마지막 값은 상한 도달까지 유지된다.
BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900, 3600)

#: 한 번의 실행이 처리할 최대 건수(§17.6 청크).
DEFAULT_BATCH = 100


def backoff_for(attempts: int) -> timedelta:
    """attempts회 실패한 뒤 다음 시도까지 기다릴 시간."""
    index = min(max(attempts, 1), len(BACKOFF_SECONDS)) - 1
    return timedelta(seconds=BACKOFF_SECONDS[index])


def dispatch_pending(*, limit: int = DEFAULT_BATCH, now: datetime | None = None) -> dict[str, int]:
    """대기 중인 이벤트를 소비한다.

    Returns:
        {"scanned", "published", "skipped", "failed", "alerts"} 집계.
        `alerts`는 실제로 새로 만들어진 알림 건수다(중복 생략분 제외).
    """
    moment = now or utcnow()
    counts = {"scanned": 0, "published": 0, "skipped": 0, "failed": 0, "alerts": 0}

    for event_id in _candidates(limit=limit, now=moment):
        counts["scanned"] += 1
        outcome, created = _process_one(event_id, now=moment)
        counts[outcome] += 1
        counts["alerts"] += created
    return counts


def _candidates(*, limit: int, now: datetime) -> list[int]:
    """보낼 차례가 된 이벤트 id (잠금 밖 가벼운 읽기 — 판단은 잠근 뒤 다시 한다)."""
    with unit_of_work() as uow:
        return list(
            uow.session.execute(
                select(Event.id)
                .where(
                    Event.published_at.is_(None),
                    Event.attempts < MAX_ATTEMPTS,
                    or_(Event.next_retry_at.is_(None), Event.next_retry_at <= now),
                )
                .order_by(Event.next_retry_at.nulls_first(), Event.id)
                .limit(limit)
            ).scalars()
        )


def _process_one(event_id: int, *, now: datetime) -> tuple[str, int]:
    try:
        with unit_of_work() as uow:
            session = uow.session
            event = session.execute(
                select(Event)
                .where(
                    Event.id == event_id,
                    Event.published_at.is_(None),
                    Event.attempts < MAX_ATTEMPTS,
                )
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if event is None:
                # 다른 디스패처가 집었거나 이미 처리됐다 — 경합의 정상 결과다.
                return "skipped", 0

            created = _fan_out(session, event)
            # 매칭 규칙이 없어도 발행 완료로 적는다 — 안 그러면 구독자 없는
            # 이벤트가 매 주기 다시 집혀 대기열이 영원히 줄지 않는다.
            event.published_at = now
            return "published", created
    except Exception as error:
        # 한 건의 실패가 나머지를 막지 않는다(§17.6) — 넓게 잡는 것이 여기서는
        # 의도다. 좁히면 예상 못 한 예외 하나가 배치 전체를 멈춘다.
        _record_failure(event_id, error=error, now=now)
        return "failed", 0


def _fan_out(session: Session, event: Event) -> int:
    """이벤트 → 구독 규칙 → 알림. 만들어진 알림 건수를 돌려준다."""
    created = 0
    payload = event.payload or {}
    #: 담당자 축은 payload가 실어 온 것만 본다 — 디스패처가 도메인 테이블을
    #: 되짚으면 이벤트마다 분기가 생기고, 그 분기가 곧 코어의 이원화다.
    #: 인증 기일 계열의 담당자 라우팅은 스캔(PR-2)이 직접 코어를 부른다.
    assignee_id = payload.get("assignee_id")
    for rule in notifications.matching_rules(session, event.event_type):
        created += len(
            notifications.notify(
                session,
                subject_key=f"event:{event.id}:{rule.id}",
                title=rule.name_ko,
                body=_body_for(event),
                severity=rule.severity,
                event_type=event.event_type,
                rule=rule,
                assignee_id=assignee_id if isinstance(assignee_id, int) else None,
                routing=notifications.Routing.EVENT,
                entity_type=event.aggregate_type,
                entity_id=event.aggregate_id,
            )
        )
    return created


def _body_for(event: Event) -> str:
    """사람이 읽을 한 줄. payload 원문을 그대로 붙이지 않는다(§18.1 — 값이
    그대로 화면·로그로 흐른다)."""
    if event.aggregate_type and event.aggregate_id:
        return f"{event.event_type} · {event.aggregate_type} #{event.aggregate_id}"
    return event.event_type


def _record_failure(event_id: int, *, error: Exception, now: datetime) -> None:
    """실패 기록은 **별도 트랜잭션**이다 — 업무 롤백과 함께 사라지면 안 된다."""
    with unit_of_work() as uow:
        session = uow.session
        event = session.execute(
            select(Event).where(Event.id == event_id).with_for_update()
        ).scalar_one_or_none()
        if event is None:
            return
        event.attempts += 1
        event.last_error = f"{type(error).__name__}: {error}"[:2000]
        event.next_retry_at = now + backoff_for(event.attempts)
        if event.attempts >= MAX_ATTEMPTS:
            # 상한 도달 = 대기열 이탈. 조용히 두면 아무도 모른다(§17.6).
            notifications.notify(
                session,
                subject_key=f"outbox.poison:events:{event.id}",
                title="아웃박스 발송이 반복 실패했습니다",
                body=(
                    f"{event.event_type} · 이벤트 #{event.id}가 {event.attempts}회 연속 "
                    "실패해 재시도 대기열에서 제외됐습니다. 원인을 확인한 뒤 조치해 주세요."
                ),
                severity="CRITICAL",
                routing=notifications.Routing.ADMIN,
                entity_type="events",
                entity_id=event.id,
            )
