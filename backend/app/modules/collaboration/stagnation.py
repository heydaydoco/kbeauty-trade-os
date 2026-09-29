"""정체 N일·다음 액션 독촉 스캔 (§5.4 "정체 N일 알림" / s2-4-plan.md §2 안건 ② — S2-4 PR-1).

★ **알림 생성 코어(`notifications.notify`)를 직접 부른다** — 기일 스캔(deadlines)과 같은 계보다
  (아웃박스 비경유 · 멱등은 코어의 `ON CONFLICT DO NOTHING` 하나 — §17.4). 발송은 없다:
  독촉은 사람이 하고, 시스템은 "누구 쪽 공이 며칠째 머무는지"를 알림센터에 올릴 뿐이다
  (§5.4 "생성까지 시스템, 발송은 사람").

■ 정체의 정의

  대상 = 활성 인증 중 상태가 {서류준비·신청제출·심사중·보완요청·갱신중}(공이 어딘가에 있는
  진행 중 5태). 미착수는 §4.8 자동 적용분이 대량일 수 있어 매트릭스 🔴가 그 신호이고, 승인·
  만료임박·만료는 기일 엔진의 몫이다.

  마지막 활동일 = max(공이 넘어간 날[없으면 생성일 — 자동 적용·이관 행이 조용히 빠지지 않게],
  최근 상태 변경일[KST], 최근 통신 기록 발생일·다음 액션 완료일). "N일간 공 이동·상태 변경·
  통신 기록이 모두 없음"이 정체다. 정체일 = 오늘(KST) − 마지막 활동일, N은 **현재 공 주체별**
  (기본 사내 7·대행사 7·기관 30 — 기관 회신은 원래 느리다).

■ N은 데이터다 (ADR-11 — 기일 문턱 `alert_rules.config.thresholds` 선례)

  `alert_rules`의 event_type `certifications.stagnation` 규칙이 `config.days`
  ({"INTERNAL": 7, "AGENCY": 7, "AUTHORITY": 30} 중 일부만 적어도 됨)로 덮어쓴다.
  형이 흐린 값은 그 키만 기본값으로 — 잘못된 config 하나로 스캔이 서는 것보다 낫다.

■ 독촉 주기 — 한 번이 아니라 N일마다 (dedup 키의 k)

  키 `stagnation:certifications:{id}:{공 주체}@{마지막 활동일}#{k}` — k = 정체일 ÷ N의 몫.
  N일마다 1건씩 반복 독촉하고(스캔이 며칠 빠져도 그 시점 몫 1건만 — 지난 몫 소급 없음),
  활동이 생기면 마지막 활동일이 바뀌어 새 에피소드가 된다.

■ 다음 액션 독촉

  완료되지 않은 통신 기록(활성 인증 소속)의 다음 액션 기한 — 기한 당일 `:DUE`(INFO)·도과
  후 `:OVERDUE`(WARN) 각 1건(기한을 고치면 새 키). 종결 인증·삭제된 인증의 기록은 제외.

■ 수신자 = 담당자 → 규칙 → ADMIN 폴백 (Routing.DEADLINE — ADR-0045)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.logging import get_logger
from app.core.logging.redaction import scrub_text
from app.core.time import to_kst, today_kst
from app.modules.certifications.machine import TERMINAL_STATUSES
from app.modules.certifications.models import ACTION_OWNERS, Certification, CertificationStatusLog
from app.modules.collaboration.models import CommLog
from app.modules.deadlines.service import ALERT_TITLE_MAX
from app.modules.notifications import service as notifications
from app.modules.notifications.service import Routing
from app.modules.partners.models import Partner
from app.modules.worklist.models import AlertRule

logger = get_logger(__name__)

#: alert_rules.event_type 값 공간(<도메인>.<대상>.<사건>) — 관리자가 이 종류로 규칙을 등록하면
#: 수신자·N을 덮어쓴다(없으면 담당자 → ADMIN 폴백, N은 기본값).
STAGNATION_EVENT = "certifications.stagnation"
FOLLOW_UP_EVENT = "comm_logs.follow_up.due"

#: 정체 대상 상태 — 공이 어딘가에 있는 진행 중 5태(모듈 독스트링 ■ 정체의 정의).
STAGNATION_STATUSES: tuple[str, ...] = (
    "PREPARING",
    "SUBMITTED",
    "IN_REVIEW",
    "SUPPLEMENTING",
    "RENEWING",
)

#: 기본 정체 기준(일) — 규칙 config.days가 주체별로 덮어쓴다.
DEFAULT_STAGNATION_DAYS: dict[str, int] = {"INTERNAL": 7, "AGENCY": 7, "AUTHORITY": 30}

#: dedup 키 종류 세그먼트.
KIND_STAGNATION = "stagnation"
KIND_FOLLOW_UP = "followup"

_OWNER_LABELS = {"INTERNAL": "사내", "AGENCY": "대행사", "AUTHORITY": "기관"}


# ── 순수 함수 ────────────────────────────────────────────────────────────────


def normalize_days(raw: Any) -> dict[str, int]:
    """규칙 config.days — 주체별 양의 정수만 받고, 아니면 그 키만 기본값.

    config는 관리자가 손으로 적는 JSON이라 형이 흐릿할 수 있다. 통째로 버리지 않고 키별로
    판정한다 — 한 키의 오타가 다른 키의 정당한 덮어쓰기를 무효로 만들면 안 된다.
    """
    days = dict(DEFAULT_STAGNATION_DAYS)
    if not isinstance(raw, dict):
        return days
    for owner in ACTION_OWNERS:
        value = raw.get(owner)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            continue
        days[owner] = value
    return days


def stagnation_period(stagnant_days: int, threshold_days: int) -> int:
    """독촉 몫 k — 0이면 아직 정체가 아니다(N일 미만). N일마다 1씩 오른다."""
    return stagnant_days // threshold_days


def last_activity_date(
    *,
    owner_changed_on: date | None,
    created_at: datetime,
    status_changed_at: datetime | None,
    comm_activity_on: date | None,
) -> date:
    """마지막 활동일(KST 업무일) — 네 원천의 최댓값. 공이 넘어간 날이 없으면 생성일로 본다."""
    candidates: list[date] = [
        owner_changed_on if owner_changed_on is not None else to_kst(created_at).date()
    ]
    if status_changed_at is not None:
        candidates.append(to_kst(status_changed_at).date())
    if comm_activity_on is not None:
        candidates.append(comm_activity_on)
    return max(candidates)


def _title(text: str) -> str:
    return text if len(text) <= ALERT_TITLE_MAX else text[: ALERT_TITLE_MAX - 1] + "…"


# ── 규칙 조회 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Policy:
    event_type: str
    rule: AlertRule | None
    days: dict[str, int]


def _policy(session: Session, event_type: str) -> Policy:
    rules = notifications.matching_rules(session, event_type)
    rule = rules[0] if rules else None
    days = normalize_days((rule.config or {}).get("days") if rule else None)
    return Policy(event_type=event_type, rule=rule, days=days)


# ── 후보 ─────────────────────────────────────────────────────────────────────


def _stagnation_candidate_ids(session: Session) -> list[int]:
    return list(
        session.execute(
            select(Certification.id)
            .where(
                Certification.deleted_at.is_(None),
                Certification.status.in_(STAGNATION_STATUSES),
            )
            .order_by(Certification.id)
        ).scalars()
    )


def _follow_up_candidate_ids(session: Session, today: date) -> list[int]:
    """기한이 오늘이거나 지난 미완료 다음 액션 — 활성·비종결 인증 소속만."""
    return list(
        session.execute(
            select(CommLog.id)
            .join(
                Certification,
                (CommLog.subject_type == "CERTIFICATION")
                & (CommLog.subject_id == Certification.id),
            )
            .where(
                CommLog.deleted_at.is_(None),
                CommLog.next_action.is_not(None),
                CommLog.next_action_done_on.is_(None),
                CommLog.next_action_due.is_not(None),
                CommLog.next_action_due <= today,
                Certification.deleted_at.is_(None),
                Certification.status.not_in(tuple(TERMINAL_STATUSES)),
            )
            .order_by(CommLog.id)
        ).scalars()
    )


# ── 스캔 본체 ────────────────────────────────────────────────────────────────


def _activity_sources(
    session: Session, certification_id: int
) -> tuple[datetime | None, date | None]:
    """최근 상태 변경 시각과 최근 통신 활동일(발생일·다음 액션 완료일 중 늦은 쪽)."""
    status_changed_at = session.execute(
        select(func.max(CertificationStatusLog.occurred_at)).where(
            CertificationStatusLog.certification_id == certification_id
        )
    ).scalar_one()
    occurred, done = session.execute(
        select(func.max(CommLog.occurred_on), func.max(CommLog.next_action_done_on)).where(
            CommLog.subject_type == "CERTIFICATION",
            CommLog.subject_id == certification_id,
            CommLog.deleted_at.is_(None),
        )
    ).one()
    dates = [value for value in (occurred, done) if value is not None]
    return status_changed_at, (max(dates) if dates else None)


def _open_follow_up_hint(session: Session, certification_id: int) -> str | None:
    """알림 본문용 — 가장 최근에 남긴 미완료 다음 액션(있으면)."""
    return session.execute(
        select(CommLog.next_action)
        .where(
            CommLog.subject_type == "CERTIFICATION",
            CommLog.subject_id == certification_id,
            CommLog.deleted_at.is_(None),
            CommLog.next_action.is_not(None),
            CommLog.next_action_done_on.is_(None),
        )
        .order_by(CommLog.occurred_on.desc(), CommLog.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _scan_stagnation_one(session: Session, certification_id: int, today: date) -> dict[str, int]:
    row = session.execute(
        select(Certification).where(
            Certification.id == certification_id,
            Certification.deleted_at.is_(None),
            Certification.status.in_(STAGNATION_STATUSES),
        )
    ).scalar_one_or_none()
    if row is None:  # 후보 수집 뒤 삭제·상태 이동된 행 — 건너뛴다
        return {}
    policy = _policy(session, STAGNATION_EVENT)
    status_changed_at, comm_activity_on = _activity_sources(session, row.id)
    last_activity = last_activity_date(
        owner_changed_on=row.action_owner_changed_on,
        created_at=row.created_at,
        status_changed_at=status_changed_at,
        comm_activity_on=comm_activity_on,
    )
    threshold = policy.days[row.action_owner]
    stagnant_days = (today - last_activity).days
    period = stagnation_period(stagnant_days, threshold)
    made = {"certifications": 1, "stagnant": 0}
    if period < 1:
        return made

    owner_label = _OWNER_LABELS[row.action_owner]
    if row.action_owner == "AGENCY" and row.agency_partner_id is not None:
        agency = session.execute(
            select(Partner.name_ko).where(Partner.id == row.agency_partner_id)
        ).scalar_one_or_none()
        if agency:
            owner_label = f"대행사({agency})"
    hint = _open_follow_up_hint(session, row.id)
    made["stagnant"] += len(
        notifications.notify(
            session,
            subject_key=(
                f"{KIND_STAGNATION}:certifications:{row.id}:{row.action_owner}"
                f"@{last_activity.isoformat()}#{period}"
            ),
            title=_title(f"정체 {stagnant_days}일 — [{row.target_type}] {row.template_name}"),
            body=(
                f"공이 {owner_label} 쪽에 {stagnant_days}일째 머물러 있습니다"
                f"(마지막 움직임 {last_activity.isoformat()}·기준 {threshold}일). "
                f"다음 액션: {hint or '기록 없음'}. "
                "진행 상황을 확인하고, 독촉이 필요하면 통신 기록에 남겨 주세요."
            ),
            severity="WARN",
            event_type=policy.event_type,
            rule=policy.rule,
            assignee_id=row.assignee_id,
            routing=Routing.DEADLINE,
            entity_type="certifications",
            entity_id=row.id,
        )
    )
    return made


def _scan_follow_up_one(session: Session, log_id: int, today: date) -> dict[str, int]:
    row = session.execute(
        select(CommLog, Certification)
        .join(
            Certification,
            (CommLog.subject_type == "CERTIFICATION") & (CommLog.subject_id == Certification.id),
        )
        .where(
            CommLog.id == log_id,
            CommLog.deleted_at.is_(None),
            CommLog.next_action.is_not(None),
            CommLog.next_action_done_on.is_(None),
            CommLog.next_action_due.is_not(None),
            Certification.deleted_at.is_(None),
            Certification.status.not_in(tuple(TERMINAL_STATUSES)),
        )
    ).one_or_none()
    if row is None:  # 후보 수집 뒤 완료·삭제·종결된 건 — 건너뜀
        return {}
    log, certification = row
    due = log.next_action_due
    assert due is not None and log.next_action is not None
    overdue = due < today
    policy = _policy(session, FOLLOW_UP_EVENT)
    label = f"[{certification.target_type}] {certification.template_name}"
    made = {"follow_ups": 1, "follow_up_due": 0, "follow_up_overdue": 0}
    created = notifications.notify(
        session,
        subject_key=(
            f"{KIND_FOLLOW_UP}:comm_logs:{log.id}@{due.isoformat()}:"
            f"{'OVERDUE' if overdue else 'DUE'}"
        ),
        title=_title(
            f"다음 액션 기한 {(today - due).days}일 지남 — {label}"
            if overdue
            else f"다음 액션 기한 오늘 — {label}"
        ),
        body=(
            f"통신 기록 #{log.id}({log.occurred_on.isoformat()})의 다음 액션 "
            f"“{log.next_action[:120]}”의 기한 {due.isoformat()}"
            f"{'이(가) 지났습니다' if overdue else '입니다'}. "
            "완료했다면 통신 기록에서 완료 처리해 주세요."
        ),
        severity="WARN" if overdue else "INFO",
        event_type=policy.event_type,
        rule=policy.rule,
        assignee_id=certification.assignee_id,
        routing=Routing.DEADLINE,
        entity_type="certifications",
        entity_id=certification.id,
    )
    made["follow_up_overdue" if overdue else "follow_up_due"] += len(created)
    return made


def _guarded(
    work: Callable[[Session, int, date], dict[str, int]],
    entity_id: int,
    *,
    today: date,
    counts: dict[str, int],
) -> None:
    """한 건 = 한 트랜잭션. 실패는 그 건만 롤백·집계하고 로그에 남긴다(§17.6).

    집계는 **커밋이 끝난 뒤** 합산한다 — 롤백된 건의 알림은 없던 일이다(기일 스캔과 같은 규율).
    """
    try:
        with unit_of_work() as uow:
            made = work(uow.session, entity_id, today)
    except Exception as error:
        counts["failed"] += 1
        logger.error(
            "stagnation_scan_subject_failed",
            work=work.__name__,
            entity_id=entity_id,
            error=scrub_text(f"{type(error).__name__}: {error}")[:500],
        )
        return
    for key, value in made.items():
        counts[key] += value


def scan_stagnation(*, base_date: date | None = None) -> dict[str, int]:
    """정체 N일·다음 액션 기한 독촉 알림을 만든다.

    건별 독립 트랜잭션(§17.6)·멱등(같은 기준일 재실행 → 신규 0 — dedup 키가 DB 유니크)·기준일
    기본 KST 오늘. 실패가 1건이라도 있으면 실행기 잡이 FAILED로 올린다(성공한 건은 커밋돼
    재실행이 덮어쓰지 않는다).

    Returns:
        {"certifications", "stagnant", "follow_ups", "follow_up_due", "follow_up_overdue",
        "failed"} — certifications·follow_ups는 스캔한 건수, 가운데 셋은 **새로** 만든 알림 수.
    """
    today = base_date or today_kst()
    counts = {
        "certifications": 0,
        "stagnant": 0,
        "follow_ups": 0,
        "follow_up_due": 0,
        "follow_up_overdue": 0,
        "failed": 0,
    }
    with unit_of_work() as uow:
        certification_ids = _stagnation_candidate_ids(uow.session)
        log_ids = _follow_up_candidate_ids(uow.session, today)
    for certification_id in certification_ids:
        _guarded(_scan_stagnation_one, certification_id, today=today, counts=counts)
    for log_id in log_ids:
        _guarded(_scan_follow_up_one, log_id, today=today, counts=counts)
    return counts
