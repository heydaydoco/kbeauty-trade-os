"""승인 정체 N일 독촉 스캔 (S3-1 ADR-0061 / design-C C7) — 잡 `approval-stagnation-scan`(daily@07:10 KST).

★ **알림만 만든다 — 승인 상태·전표를 바꾸지 않는다**(만료·자동 결정 없음). 읽기 + `notifications.notify` INSERT뿐이라 §15 L3 4금(지출·법적 판정·대외 최초
  발송·장부 확정)에 저촉하지 않는다(ADR-0061 4금 논증). 이 모듈은 `service`(결정·소비·무효·요청 함수)를 **임포트하지 않는다** — 잡·CLI가 승인 상태를
  바꾸는 함수에 닿지 못하게 하는 아키텍처 스캔의 대상이다.
■ 대상 = REQUESTED 승인. 경과일 = 오늘(KST 달력 날짜) − 요청일(KST 날짜). 경과일 ≥ N이면 요청 알림과 **같은 수신자 결정**(`notification_recipients`)으로
  독촉한다. **N 기본 2일**(결재가 전표 확정을 막는 업무라 인증 정체 7일보다 짧게) — `alert_rules` event_type `approvals.stagnation`의 `config.days`(정수)가
  덮어쓰고, 형이 흐리면 기본값이다(정체 스캔 선례).
■ dedup 키 `approval:{id}:stagnation#{k}`, k = 경과일 ÷ N의 몫 — N일마다 1건(스캔이 며칠 빠져도 그 시점 몫 1건만, 소급 없음). 건별 독립 트랜잭션,
  실패가 1건이라도 있으면 실행기가 잡을 FAILED로 올린다(`failed` 집계).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.logging import get_logger
from app.core.logging.redaction import scrub_text
from app.core.time import to_kst
from app.modules.approvals import alerts
from app.modules.approvals.authority import kst_today
from app.modules.approvals.machine import ApprovalStatus
from app.modules.approvals.models import Approval
from app.modules.notifications import service as notifications
from app.modules.worklist.models import AlertRule

logger = get_logger(__name__)

#: 기본 정체 기준(일) — 규칙 `config.days`가 덮어쓴다.
DEFAULT_STAGNATION_DAYS = 2


def normalize_days(raw: Any) -> int:
    """규칙 config.days — 양의 정수만 받고, 아니면 기본값(잘못된 config 하나로 스캔이 서는 것보다 낫다)."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return DEFAULT_STAGNATION_DAYS
    return raw


def stagnation_period(stagnant_days: int, threshold_days: int) -> int:
    """독촉 몫 k — 0이면 아직 정체가 아니다(N일 미만). N일마다 1씩 오른다."""
    return stagnant_days // threshold_days


def _policy(session: Session) -> tuple[AlertRule | None, int]:
    rules = notifications.matching_rules(session, alerts.STAGNATION_EVENT)
    rule = rules[0] if rules else None
    config = rule.config if rule is not None and isinstance(rule.config, dict) else {}
    return rule, normalize_days(config.get("days"))


def _scan_one(session: Session, approval_id: int, today: date) -> dict[str, int]:
    approval = session.execute(
        select(Approval).where(
            Approval.id == approval_id, Approval.status == ApprovalStatus.REQUESTED.value
        )
    ).scalar_one_or_none()
    if approval is None:  # 후보 수집 뒤 결정·회수·무효된 건 — 건너뛴다
        return {}
    rule, threshold = _policy(session)
    stagnant_days = (today - to_kst(approval.created_at).date()).days
    period = stagnation_period(stagnant_days, threshold)
    made = {"approvals": 1, "stagnant": 0}
    if period < 1:
        return made
    made["stagnant"] += alerts.notify_stagnation(
        session, approval, stagnant_days=stagnant_days, period=period, rule=rule
    )
    return made


def _guarded(
    work: Callable[[Session, int, date], dict[str, int]],
    entity_id: int,
    *,
    today: date,
    counts: dict[str, int],
) -> None:
    """한 건 = 한 트랜잭션. 실패는 그 건만 롤백·집계하고 로그에 남긴다(§17.6). 집계는 커밋이 끝난 뒤에 합산한다."""
    try:
        with unit_of_work() as uow:
            made = work(uow.session, entity_id, today)
    except Exception as error:
        counts["failed"] += 1
        logger.error(
            "approval_stagnation_scan_failed",
            entity_id=entity_id,
            error=scrub_text(f"{type(error).__name__}: {error}")[:500],
        )
        return
    for key, value in made.items():
        counts[key] += value


def scan_approval_stagnation(*, base_date: date | None = None) -> dict[str, int]:
    """결재 대기 정체 독촉 알림을 만든다.

    건별 독립 트랜잭션·멱등(같은 기준일 재실행 → 신규 0 — dedup 키가 DB 유니크)·기준일 기본 KST 오늘.

    Returns:
        {"approvals", "stagnant", "failed"} — approvals는 스캔한 건수, stagnant는 **새로** 만든 알림 수.
    """
    today = base_date or kst_today()
    counts = {"approvals": 0, "stagnant": 0, "failed": 0}
    with unit_of_work() as uow:
        ids = list(
            uow.session.execute(
                select(Approval.id)
                .where(Approval.status == ApprovalStatus.REQUESTED.value)
                .order_by(Approval.id)
            ).scalars()
        )
    for approval_id in ids:
        _guarded(_scan_one, approval_id, today=today, counts=counts)
    return counts
