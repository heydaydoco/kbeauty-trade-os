"""기일 엔진 1차 — 인증·문서 유효기간 스캔·도과·에스컬레이션·브리핑 (§4.7 / §2 ADR-07 / GC-C1 / S2-3 PR-2).

★ **스캔은 알림 생성 코어(`notifications.notify`)를 직접 부른다**(판정 요청 3 (가)).
  아웃박스를 경유하지 않는 이유: 발행→소비 창에서 같은 기일이 두 번 발행되면
  이벤트는 둘, 알림은 dedup으로 하나 — "확인 후 INSERT"를 스캔이 하게 되는 셈이고
  §17.4가 그 패턴을 금지한다. 코어의 `ON CONFLICT DO NOTHING`이 유일한 멱등 장치다.

★ **문턱 통과 의미론은 "정각"이 아니라 "지났다"다**(GC-C1 정본). 오늘 D-88이면
  D-90분은 이미 발송된 상태여야 한다 — 어제 스캔이 죽었어도 오늘 스캔이 D-90을
  보낸다(지각 발송). 다음 문턱(D-30)은 아직 안 보낸다. 문턱 목록은 데이터다
  (`alert_rules.config.thresholds` — ADR-11), 없으면 기본 D-180/90/30.

★ **dedup 키의 문턱 세그먼트는 만료일을 품는다** — `D-90@2027-09-30`. 인증
  인스턴스는 갱신 주기를 거쳐 **같은 행**이 새 만료일을 얻는다(만료 포함 유니크 —
  만료 후 재개=기존 행 경유). 키에 만료일이 없으면 두 번째 주기의 D-180은 첫
  주기의 행에 막혀 영원히 안 나간다 — 조용한 도과, 이 축이 존재하는 이유의 정반대.
  만료일 정정도 같은 이치로 새 기일이라 새 알림이다(GC-C1 "동일 만료 건" 문면).

★ **수신자는 기일 계열 이원화다**(ADR-0045 — 담당자 → 규칙 → ADMIN 폴백). 인증은
  `assignee_id`가 담당자이고, 문서는 담당자 컬럼이 없어 규칙·폴백 경로만 탄다.

★ **D-3 에스컬레이션은 "미확인이 있을 때"만 발동한다**(DoD "미확인 에스컬레이션
  발동"). 판정은 이번 스캔이 새로 만드는 알림보다 **앞서** 한다 — 오늘 처음 D-30을
  받은 사람이 같은 순간 관리자 에스컬레이션까지 유발하면 "안 읽었다"가 아니라
  "받을 틈이 없었다"인데도 발동하는 꼴이다. 수신자는 ADMIN 전원(판정 요청 8),
  키는 `esc:` 접두(요청 6).

★ **갱신중 도과는 상태 전이가 아니라 계산값이다**(안건 ⑦ — RENEWING은 스윕
  비대상). 이 파일은 도과 알림만 만들고, 표시 계산값은 certifications 뷰의 몫이다.

★ **스캔은 활성 행만 본다**(요청 19 동석 문면) — `deleted_at IS NULL`. 그래서
  태스크가 참조 중인 문서의 soft delete는 409다(documents.service): 삭제되는 순간
  그 문서의 만료 알림이 조용히 사라지기 때문이다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.logging import get_logger
from app.core.logging.redaction import scrub_text
from app.core.time import today_kst
from app.modules.certifications.machine import TERMINAL_STATUSES
from app.modules.certifications.models import Certification
from app.modules.documents.models import Document, DocumentType
from app.modules.identity.models import User
from app.modules.notifications import service as notifications
from app.modules.notifications.service import Routing
from app.modules.worklist.models import Alert, AlertRule

logger = get_logger(__name__)

#: 기본 문턱 (GC-C1 예시 문면 "D-180/D-90/D-30"). 규칙 config가 덮어쓴다.
DEFAULT_THRESHOLDS: tuple[int, ...] = (180, 90, 30)

#: 에스컬레이션 발동 문턱 — D-3 (WBS S2-3 산출물 문면).
ESCALATION_DAYS = 3

#: 이 문턱 이하의 기일 알림은 긴급(CRITICAL) 등급 — 그 위는 주의(WARN).
CRITICAL_THRESHOLD_DAYS = 30

#: 기일 이벤트 종류 — alert_rules.event_type 값 공간(<도메인>.<대상>.<사건>).
CERTIFICATION_EVENT = "certifications.expiry.approaching"
DOCUMENT_EVENT = "documents.validity.approaching"
BRIEFING_EVENT = "notifications.briefing.daily"

#: dedup 키 종류 세그먼트.
KIND_DEADLINE = "deadline"
KIND_ESCALATION = "esc"
KIND_BRIEFING = "briefing"


# ── 순수 함수 ────────────────────────────────────────────────────────────────


def days_left(expires_on: date, base_date: date) -> int:
    """만료일까지 남은 일수 — 음수면 도과."""
    return (expires_on - base_date).days


def passed_thresholds(remaining: int, thresholds: tuple[int, ...]) -> tuple[int, ...]:
    """이미 지난 문턱 전부 (GC-C1 — D-88이면 (180, 90), D-30은 아직).

    도과(remaining < 0)면 문턱 전부가 지난 것이다 — 도과 알림과 별개로 문턱
    알림도 성립한다(어제까지 스캔이 한 번도 안 돌았다면 지금이라도 나간다).
    """
    return tuple(sorted((t for t in thresholds if remaining <= t), reverse=True))


def threshold_label(threshold: int) -> str:
    return f"D-{threshold}"


def _normalize_thresholds(raw: Any) -> tuple[int, ...]:
    """규칙 config의 thresholds — 양의 정수 목록만 받고, 아니면 기본값.

    config는 관리자가 손으로 적는 JSON이라 형이 흐릿할 수 있다. 잘못된 값으로
    스캔 전체가 서는 것보다 기본값으로 도는 편이 낫다(fail-visible은 알림
    자체의 몫이지 문턱 목록의 몫이 아니다).
    """
    if not isinstance(raw, list) or not raw:
        return DEFAULT_THRESHOLDS
    cleaned: set[int] = set()
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            return DEFAULT_THRESHOLDS
        cleaned.add(item)
    return tuple(sorted(cleaned, reverse=True))


# ── 규칙 조회 (문턱 데이터화 — ADR-11) ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Policy:
    event_type: str
    rule: AlertRule | None
    thresholds: tuple[int, ...]


def _policy(session: Session, event_type: str) -> Policy:
    """이 기일 축의 활성 규칙(첫 번째)과 문턱 목록.

    규칙은 사건 종류당 하나가 정상이다 — 둘 이상이면 id가 작은 것이 정본이고
    나머지는 무시한다(수신자 팬아웃은 규칙이 아니라 역할로 한다 — ADR-07).
    """
    rules = notifications.matching_rules(session, event_type)
    rule = rules[0] if rules else None
    thresholds = _normalize_thresholds((rule.config or {}).get("thresholds") if rule else None)
    return Policy(event_type=event_type, rule=rule, thresholds=thresholds)


# ── 스캔 대상 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Subject:
    """기일을 가진 한 건 — 인증이든 문서든 같은 모양으로 스캔한다."""

    entity_type: str  # "certifications" | "documents"
    entity_id: int
    expires_on: date
    title: str
    assignee_id: int | None


def _certification_subject(row: Certification) -> Subject:
    return Subject(
        entity_type="certifications",
        entity_id=row.id,
        expires_on=row.expires_on,  # type: ignore[arg-type]  # 후보 질의가 NOT NULL을 보장
        title=f"[{row.target_type}] {row.template_name}",
        assignee_id=row.assignee_id,
    )


def _document_subject(session: Session, row: Document) -> Subject:
    type_name = session.execute(
        select(DocumentType.name_ko).where(DocumentType.id == row.document_type_id)
    ).scalar_one()
    return Subject(
        entity_type="documents",
        entity_id=row.id,
        expires_on=row.valid_until,  # type: ignore[arg-type]
        title=f"{type_name} · {row.owner_type} #{row.owner_id}",
        assignee_id=None,  # 문서에는 담당자 컬럼이 없다 — 규칙·폴백 경로
    )


def _certification_candidate_ids(session: Session) -> list[int]:
    """활성이면서 만료일이 있고 종결(반려·중단)이 아닌 인증 — 상태 무관.

    RENEWING도 포함이다(갱신중 도과 — 안건 ⑦). 무기한(NULL)은 기일이 없다.
    """
    return list(
        session.execute(
            select(Certification.id)
            .where(
                Certification.deleted_at.is_(None),
                Certification.expires_on.is_not(None),
                Certification.status.not_in(tuple(TERMINAL_STATUSES)),
            )
            .order_by(Certification.id)
        ).scalars()
    )


def _document_candidate_ids(session: Session) -> list[int]:
    """활성이면서 유효기간이 있는 문서 (§4.7 "유효기간 있는 모든 문서")."""
    return list(
        session.execute(
            select(Document.id)
            .where(Document.deleted_at.is_(None), Document.valid_until.is_not(None))
            .order_by(Document.id)
        ).scalars()
    )


# ── 스캔 본체 ────────────────────────────────────────────────────────────────


def _subject_key(kind: str, subject: Subject, threshold: str) -> str:
    """`{종류}:{대상type}:{대상id}:{문턱}` — 코어가 `:{수신자id}`를 붙인다(ADR-0045)."""
    return f"{kind}:{subject.entity_type}:{subject.entity_id}:{threshold}"


def _has_unacknowledged_deadline_alert(session: Session, subject: Subject) -> bool:
    """이 건의 기일 알림 중 미확인이 하나라도 남아 있는가 (에스컬레이션 판정)."""
    prefix = _subject_key(KIND_DEADLINE, subject, "")
    return bool(
        session.execute(
            select(
                exists().where(
                    Alert.dedup_key.like(f"{prefix}%"),
                    Alert.acknowledged_at.is_(None),
                    Alert.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


def _scan_subject(
    session: Session, subject: Subject, *, policy: Policy, base_date: date, counts: dict[str, int]
) -> None:
    """한 건의 기일 판정 — 에스컬레이션 → 도과 → 문턱 순서(독스트링 ★ D-3 참고)."""
    remaining = days_left(subject.expires_on, base_date)
    stamp = subject.expires_on.isoformat()

    # ① 에스컬레이션 — D-3 이내(도과 포함)이고 미확인 기일 알림이 남아 있을 때.
    #    이번 스캔이 만들 알림보다 먼저 판정한다(받을 틈이 없던 알림은 미확인이 아니다).
    if remaining <= ESCALATION_DAYS and _has_unacknowledged_deadline_alert(session, subject):
        counts["escalated"] += len(
            notifications.notify(
                session,
                subject_key=_subject_key(KIND_ESCALATION, subject, f"D-{ESCALATION_DAYS}@{stamp}"),
                title=f"미확인 기일 에스컬레이션 — {subject.title}",
                body=(
                    f"만료일 {stamp}(D-{remaining})까지 {ESCALATION_DAYS}일 이내인데 담당자가 "
                    "기일 알림을 확인하지 않았습니다. 담당자와 진행 상황을 확인해 주세요."
                ),
                severity="CRITICAL",
                routing=Routing.ADMIN,
                entity_type=subject.entity_type,
                entity_id=subject.entity_id,
            )
        )

    # ② 도과 — 만료일이 지났다(갱신중이면 "갱신중 도과" — 상태는 건드리지 않는다).
    if remaining < 0:
        counts["overdue"] += len(
            notifications.notify(
                session,
                subject_key=_subject_key(KIND_DEADLINE, subject, f"overdue@{stamp}"),
                title=f"기일 도과 — {subject.title}",
                body=f"만료일 {stamp}이 {-remaining}일 지났습니다. 갱신 진행 상황을 확인해 주세요.",
                severity="CRITICAL",
                event_type=policy.event_type,
                rule=policy.rule,
                assignee_id=subject.assignee_id,
                routing=Routing.DEADLINE,
                entity_type=subject.entity_type,
                entity_id=subject.entity_id,
            )
        )

    # ③ 문턱 — 지난 문턱 전부(지각 발송 포함). 이미 있는 키는 코어가 생략한다.
    #    D-30 이하는 긴급, 그 위는 주의 — 등급은 표시 힌트이지 라우팅 축이 아니다.
    for threshold in passed_thresholds(remaining, policy.thresholds):
        counts["threshold"] += len(
            notifications.notify(
                session,
                subject_key=_subject_key(
                    KIND_DEADLINE, subject, f"{threshold_label(threshold)}@{stamp}"
                ),
                title=f"만료 {threshold_label(threshold)} — {subject.title}",
                body=(
                    f"만료일 {stamp}까지 {remaining}일 남았습니다"
                    f"({threshold_label(threshold)} 문턱). 갱신 준비를 확인해 주세요."
                ),
                severity="CRITICAL" if threshold <= CRITICAL_THRESHOLD_DAYS else "WARN",
                event_type=policy.event_type,
                rule=policy.rule,
                assignee_id=subject.assignee_id,
                routing=Routing.DEADLINE,
                entity_type=subject.entity_type,
                entity_id=subject.entity_id,
            )
        )


def scan_deadlines(*, base_date: date | None = None) -> dict[str, int]:
    """인증 만료일·문서 유효기간을 스캔해 기일 알림을 만든다.

    건별 독립 트랜잭션(§17.6 — 한 건의 실패가 스캔을 막지 않는다)·멱등(같은
    기준일 재실행 → 신규 0 — dedup 키가 DB 유니크)·기준일 기본 KST 오늘.

    Returns:
        {"certifications", "documents", "threshold", "overdue", "escalated", "failed"} —
        앞 둘은 스캔한 건수, 가운데 셋은 **새로** 만들어진 알림 수(중복 생략분
        제외), failed는 건별 트랜잭션이 실패한 건수(로그에 사유).
    """
    effective = base_date or today_kst()
    counts = {
        "certifications": 0,
        "documents": 0,
        "threshold": 0,
        "overdue": 0,
        "escalated": 0,
        "failed": 0,
    }

    with unit_of_work() as uow:
        certification_ids = _certification_candidate_ids(uow.session)
        document_ids = _document_candidate_ids(uow.session)

    for certification_id in certification_ids:
        _scan_one(_scan_certification, certification_id, base_date=effective, counts=counts)
    for document_id in document_ids:
        _scan_one(_scan_document, document_id, base_date=effective, counts=counts)
    return counts


def _scan_one(
    work: Callable[[Session, int, date, dict[str, int]], str],
    entity_id: int,
    *,
    base_date: date,
    counts: dict[str, int],
) -> None:
    """한 건 = 한 트랜잭션. 실패는 그 건만 롤백·집계하고 로그에 남긴다(§17.6).

    넓게 잡는 것이 의도다 — 좁히면 예상 못 한 예외 하나가 그날 스캔 전체를 멈추고,
    멈춘 스캔은 조용한 도과다. 예외 문자열은 마스킹을 거친다(함정 ④ 계보).
    """
    try:
        with unit_of_work() as uow:
            work(uow.session, entity_id, base_date, counts)
    except Exception as error:
        counts["failed"] += 1
        logger.error(
            "deadline_scan_subject_failed",
            work=work.__name__,
            entity_id=entity_id,
            error=scrub_text(f"{type(error).__name__}: {error}")[:500],
        )


def _scan_certification(
    session: Session, certification_id: int, base_date: date, counts: dict[str, int]
) -> str:
    """돌아오는 값은 로그·집계용 표식일 뿐이다 — 판단은 counts가 담는다."""
    row = session.execute(
        select(Certification).where(
            Certification.id == certification_id,
            Certification.deleted_at.is_(None),
            Certification.expires_on.is_not(None),
            Certification.status.not_in(tuple(TERMINAL_STATUSES)),
        )
    ).scalar_one_or_none()
    if row is None:  # 후보 수집 뒤 삭제·종결·무기한 정정된 행 — 건너뛴다
        return "skipped"
    counts["certifications"] += 1
    _scan_subject(
        session,
        _certification_subject(row),
        policy=_policy(session, CERTIFICATION_EVENT),
        base_date=base_date,
        counts=counts,
    )
    return "scanned"


def _scan_document(
    session: Session, document_id: int, base_date: date, counts: dict[str, int]
) -> str:
    row = session.execute(
        select(Document).where(
            Document.id == document_id,
            Document.deleted_at.is_(None),
            Document.valid_until.is_not(None),
        )
    ).scalar_one_or_none()
    if row is None:
        return "skipped"
    counts["documents"] += 1
    _scan_subject(
        session,
        _document_subject(session, row),
        policy=_policy(session, DOCUMENT_EVENT),
        base_date=base_date,
        counts=counts,
    )
    return "scanned"


# ── 데일리 브리핑 (판정 요청 9 — 담당 건 보유 사용자만 각자 1통) ─────────────


@dataclass(frozen=True, slots=True)
class BriefingLine:
    label: str
    count: int


def _briefing_lines(session: Session, user_id: int, base_date: date) -> list[BriefingLine]:
    """담당 건 요약 — 진행·임박·도과·미확인 알림 수. 전부 계산값이다."""
    active = (
        Certification.assignee_id == user_id,
        Certification.deleted_at.is_(None),
        Certification.status.not_in(tuple(TERMINAL_STATUSES)),
    )

    def count(*extra: Any) -> int:
        value: int = session.execute(
            select(func.count()).select_from(Certification).where(*active, *extra)
        ).scalar_one()
        return value

    unread: int = session.execute(
        select(func.count())
        .select_from(Alert)
        .where(
            Alert.recipient_user_id == user_id,
            Alert.acknowledged_at.is_(None),
            Alert.deleted_at.is_(None),
        )
    ).scalar_one()
    return [
        BriefingLine("담당 인증(진행 중)", count()),
        BriefingLine("만료임박", count(Certification.status == "EXPIRING")),
        BriefingLine(
            "만료·도과",
            count(Certification.expires_on.is_not(None), Certification.expires_on < base_date),
        ),
        BriefingLine("미확인 알림", unread),
    ]


def _briefing_recipient_ids(session: Session) -> list[int]:
    """담당 건을 하나라도 가진 **활성** 사용자 (비활성은 어느 경로에서도 수신자가 아니다)."""
    return list(
        session.execute(
            select(User.id)
            .where(
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                exists().where(
                    Certification.assignee_id == User.id,
                    Certification.deleted_at.is_(None),
                    Certification.status.not_in(tuple(TERMINAL_STATUSES)),
                ),
            )
            .order_by(User.id)
        ).scalars()
    )


def send_daily_briefing(*, base_date: date | None = None) -> dict[str, int]:
    """담당 건 보유 사용자에게 하루 1통 — 키 `briefing:{KST 날짜}:{수신자id}`.

    ★ 키 표기 확정(PR-1 관찰 등재의 첫 판단): 규약 문면 `briefing:{수신자id}:{KST날짜}`는
      코어가 수신자를 **뒤에** 붙이는 구조와 순서가 어긋나므로, 코어를 넓히지 않고
      표기를 코어 구조에 맞춘다 — 담당 이관의 키 재작성(`_rewrite_alert_dedup_keys`)이
      "꼬리=수신자" 전제로 돌기 때문에 수신자를 앞에 두면 그 경로도 깨진다.
    ★ 수신자 결정은 `assignee_id=수신자`로 DEADLINE 경로를 태운다 — 활성 사용자만
      골라 넘기므로 폴백은 발동하지 않는다(발동하면 브리핑이 관리자에게 가는 오류 —
      테스트가 고정).
    """
    effective = base_date or today_kst()
    counts = {"recipients": 0, "sent": 0}
    with unit_of_work() as uow:
        recipient_ids = _briefing_recipient_ids(uow.session)

    for user_id in recipient_ids:
        with unit_of_work() as uow:
            session = uow.session
            counts["recipients"] += 1
            lines = _briefing_lines(session, user_id, effective)
            body = "\n".join(f"· {line.label}: {line.count}건" for line in lines)
            created = notifications.notify(
                session,
                subject_key=f"{KIND_BRIEFING}:{effective.isoformat()}",
                title=f"데일리 브리핑 — {effective.isoformat()}",
                body=body,
                severity="INFO",
                event_type=BRIEFING_EVENT,
                assignee_id=user_id,
                routing=Routing.DEADLINE,
                entity_type="certifications",
                entity_id=None,
            )
            counts["sent"] += len(created)
    return counts
