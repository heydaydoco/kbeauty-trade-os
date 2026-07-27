"""아웃박스 기록 (DESIGN.md §17.1).

발송은 하지 않는다 — **적기만 한다**. 디스패처와 채널 어댑터는 S2-3이다.
그 분리가 이 모듈의 전부이자 요점이다: 업무 트랜잭션은 외부를 모른 채 끝나고,
커밋된 뒤에야 바깥으로 나간다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.outbox.models import Event


def publish(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str | None = None,
    aggregate_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """ "이 일이 일어났다"를 업무와 같은 트랜잭션에 적는다.

    ★ 반드시 업무 세션을 그대로 받는다. 여기서 별도 트랜잭션을 열면 업무가
      롤백돼도 이벤트만 살아남아, 일어나지 않은 일이 대외로 나간다.

    payload에 비밀번호·토큰·원가를 넣지 않는다(§18.1) — 이 값은 그대로
    외부 채널로 나갈 수 있다.
    """
    event = Event(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload or {},
    )
    session.add(event)
    return event


def pending(session: Session, *, limit: int = 100) -> list[Event]:
    """아직 발송되지 않은 이벤트 (디스패처가 쓸 대기열 — 실행은 S2-3)."""
    return list(
        session.execute(
            select(Event).where(Event.published_at.is_(None)).order_by(Event.id).limit(limit)
        ).scalars()
    )
