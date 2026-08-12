"""알림 생성 코어·수신자 결정·알림센터 (ADR-07 / ADR-0012 / §18.1 소유권).

★ **알림을 만드는 통로는 `notify()` 하나뿐이다**(판정 조건 B). 기일 스캔(PR-2)도
  아웃박스 디스패처도 같은 함수를 부른다 — 통로가 둘이면 dedup 규약과 수신자
  규칙이 두 벌이 되고, 그 순간 "중복 발송 0"은 한쪽에서만 성립한다. Alert를
  직접 만드는 코드가 이 파일 밖에 없다는 사실은 아키텍처 테스트가 고정한다.

★ **dedup_key는 수신자를 포함한다**(판정 요청 6). `alerts.dedup_key`는 테이블
  전역 부분 유니크이고 `recipient_user_id`는 NOT NULL이라, 한 사건을 여러
  사람에게 보내면 행이 사람 수만큼 필요하다. 키에 수신자가 없으면 두 번째
  사람부터 ON CONFLICT DO NOTHING으로 조용히 사라진다 — 알림이 안 갔는데도
  "중복 발송 0"은 통과하는 상태가 된다. 규약:
      기일        `{종류}:{대상type}:{대상id}:{문턱}:{수신자id}`
      브리핑      `briefing:{수신자id}:{KST 날짜}`
      에스컬레이션 `esc:` 접두 + 원 키
  이 파일은 수신자 앞까지(`subject_key`)를 받아 `:{수신자id}`를 붙인다.

★ **멱등은 확인이 아니라 DB 유니크에 부딪히는 INSERT다**(§17.4). "있는지 보고
  없으면 넣는다"는 두 스캔 사이에서 뚫린다 — ON CONFLICT DO NOTHING으로
  경합을 DB에 맡기고, 생략된 건수를 그대로 돌려준다.

★ **수신자 결정은 이원화다**(판정 요청 7). 공통 순서는 규칙의 명시 수신자 →
  담당자이고, 둘 다 없을 때만 갈린다:
      DEADLINE(기일 계열) → **ADMIN 역할 전원 폴백**(fail-visible).
          기일 알림이 안 가는 것은 곧 도과이고, 도과의 대가는 인증 실효다.
          조용한 미발송보다 "규칙이 없어 관리자에게 갔다"가 낫다.
      EVENT(디스패처 일반 이벤트) → **미발송**. 이쪽은 규칙이 곧 구독 의사이고,
          폭을 넓히면 ADR-07이 금지한 전사 폭포에 가까워진다.
  **담당자가 있으면 ADMIN 폴백은 발동하지 않는다**(배타 라우팅 — 조건 E).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import ForbiddenError, NotFoundError, VersionConflictError
from app.core.time import utcnow
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import Role, RoleCode, User, UserRole
from app.modules.identity.service import AuthenticatedUser
from app.modules.worklist.models import Alert, AlertRule

RULE_CREATE_ENDPOINT = "POST /api/v1/alert-rules"

#: 폴백으로 관리자에게 간 알림의 본문 꼬리말 — 왜 내게 왔는지를 본문이 말한다.
FALLBACK_NOTE = "※ 이 알림은 수신 대상 규칙이 없어 관리자에게 전달되었습니다."


class Routing(StrEnum):
    """수신자 결정 방식 (판정 요청 7 이원화 + 운영 알림)."""

    #: 기일 계열 — 규칙·담당자가 없으면 ADMIN 전원 폴백.
    DEADLINE = "DEADLINE"
    #: 디스패처 일반 이벤트 — 규칙이 없으면 보내지 않는다.
    EVENT = "EVENT"
    #: 운영 알림(배치 실패·포이즌 이벤트) — 관리자가 **정본 수신자**다.
    #: 폴백이 아니므로 본문에 "규칙이 없어서" 꼬리말을 붙이지 않는다
    #: (§15 "실패 시 관리자 알림"·§17.6).
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class Recipients:
    user_ids: tuple[int, ...]
    #: ADMIN 폴백으로 결정됐는가 (본문 꼬리말·테스트 단언용).
    fallback: bool


@dataclass(frozen=True, slots=True)
class AlertView:
    id: int
    alert_rule_id: int | None
    recipient_user_id: int
    title: str
    body: str | None
    severity: str
    dedup_key: str
    acknowledged_at: str | None
    entity_type: str | None
    entity_id: int | None


@dataclass(frozen=True, slots=True)
class AlertRuleView:
    id: int
    code: str
    name_ko: str
    event_type: str
    severity: str
    recipient_user_id: int | None
    recipient_role: str | None
    is_enabled: bool
    version: int


# ── 수신자 결정 ──────────────────────────────────────────────────────────────


def _users_with_role(session: Session, role: RoleCode) -> tuple[int, ...]:
    """활성 계정 중 해당 역할 보유자 (겸직이라 user_roles 경유)."""
    rows = session.execute(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.code == role.value,
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .order_by(User.id)
    ).scalars()
    return tuple(dict.fromkeys(rows))


def _is_active_user(session: Session, user_id: int) -> bool:
    return (
        session.execute(
            select(User.id).where(
                User.id == user_id, User.deleted_at.is_(None), User.is_active.is_(True)
            )
        ).scalar_one_or_none()
        is not None
    )


def resolve_recipients(
    session: Session,
    *,
    rule: AlertRule | None,
    assignee_id: int | None,
    routing: Routing,
) -> Recipients:
    """누가 이 알림을 받는가 (파일 독스트링의 이원화 규칙).

    ★ 비활성·삭제 계정은 어느 경로에서도 수신자가 되지 않는다 — 퇴사자 앞으로
      쌓이는 알림은 아무도 읽지 않는 알림이다(§2 계정 비활성 절차).
    """
    if routing is Routing.ADMIN:
        # 운영 알림은 관리자가 정본 수신자다 — 규칙·담당자를 보지 않는다.
        return Recipients(_users_with_role(session, RoleCode.ADMIN), fallback=False)

    if rule is not None and rule.recipient_user_id is not None:
        if _is_active_user(session, rule.recipient_user_id):
            return Recipients((rule.recipient_user_id,), fallback=False)
        return Recipients((), fallback=False)

    if rule is not None and rule.recipient_role is not None:
        return Recipients(_users_with_role(session, RoleCode(rule.recipient_role)), fallback=False)

    if assignee_id is not None and _is_active_user(session, assignee_id):
        # 담당자가 있으면 여기서 끝난다 — ADMIN 폴백은 발동하지 않는다(조건 E).
        return Recipients((assignee_id,), fallback=False)

    if routing is Routing.DEADLINE:
        return Recipients(_users_with_role(session, RoleCode.ADMIN), fallback=True)
    return Recipients((), fallback=False)


# ── 알림 생성 코어 (유일 통로 — 조건 B) ──────────────────────────────────────


def notify(
    session: Session,
    *,
    subject_key: str,
    title: str,
    body: str | None = None,
    severity: str = "INFO",
    event_type: str | None = None,
    rule: AlertRule | None = None,
    assignee_id: int | None = None,
    routing: Routing = Routing.EVENT,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> list[int]:
    """수신자를 정하고 알림을 만든다. 이미 있는 키는 조용히 생략한다.

    Returns:
        새로 만들어진 alert id 목록. 중복으로 생략된 건은 들어 있지 않다 —
        "몇 건이 새로 갔는가"가 곧 dedup 실측치다.

    ★ 업무 세션을 그대로 받는다. 알림 생성은 스캔·디스패치 트랜잭션의 일부이고,
      여기서 따로 커밋하면 롤백된 사건의 알림만 남는다.
    """
    recipients = resolve_recipients(session, rule=rule, assignee_id=assignee_id, routing=routing)
    if not recipients.user_ids:
        return []

    full_body = body
    if recipients.fallback:
        full_body = f"{body}\n{FALLBACK_NOTE}" if body else FALLBACK_NOTE

    created: list[int] = []
    for user_id in recipients.user_ids:
        statement = (
            pg_insert(Alert)
            .values(
                alert_rule_id=rule.id if rule is not None else None,
                recipient_user_id=user_id,
                title=title,
                body=full_body,
                severity=severity,
                dedup_key=f"{subject_key}:{user_id}",
                entity_type=entity_type,
                entity_id=entity_id,
            )
            # 부분 유니크라 충돌 대상에 술어까지 줘야 PG가 그 인덱스를 집는다.
            .on_conflict_do_nothing(
                index_elements=["dedup_key"],
                index_where=Alert.deleted_at.is_(None),
            )
            .returning(Alert.id)
        )
        new_id = session.execute(statement).scalar_one_or_none()
        if new_id is not None:
            created.append(new_id)
    if event_type is not None and created:
        # 어떤 사건이 알림이 됐는지는 로그 컨텍스트로만 남긴다(본문은 title/body).
        session.info.setdefault("notified_event_types", []).append(event_type)
    return created


def matching_rules(session: Session, event_type: str) -> list[AlertRule]:
    """이 이벤트를 구독하는 활성 규칙 (디스패처의 평가 대상)."""
    return list(
        session.execute(
            select(AlertRule)
            .where(
                AlertRule.event_type == event_type,
                AlertRule.is_enabled.is_(True),
                AlertRule.deleted_at.is_(None),
            )
            .order_by(AlertRule.id)
        ).scalars()
    )


# ── 알림센터 (§18.1 — 내 알림만) ─────────────────────────────────────────────


def _alert_view(row: Alert) -> AlertView:
    return AlertView(
        id=row.id,
        alert_rule_id=row.alert_rule_id,
        recipient_user_id=row.recipient_user_id,
        title=row.title,
        body=row.body,
        severity=row.severity,
        dedup_key=row.dedup_key,
        acknowledged_at=row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
    )


def _mine(query: Select[Any], actor: AuthenticatedUser) -> Select[Any]:
    """알림은 수신자의 것이다 — 관리자도 남의 알림함을 보지 않는다.

    태스크(소유=담당자·생성자, 관리자는 전부)와 규칙이 다른 이유: 알림함은
    개인 수신함이라 "관리자는 전부 본다"가 곧 전 직원 수신함 열람이 된다.
    """
    return query.where(Alert.recipient_user_id == actor.id, Alert.deleted_at.is_(None))


def list_alerts(
    *, actor: AuthenticatedUser, offset: int, limit: int, unacknowledged_only: bool = False
) -> tuple[list[AlertView], int]:
    with unit_of_work() as uow:
        session = uow.session
        base = _mine(select(Alert), actor)
        counted = _mine(select(func.count()).select_from(Alert), actor)
        if unacknowledged_only:
            base = base.where(Alert.acknowledged_at.is_(None))
            counted = counted.where(Alert.acknowledged_at.is_(None))
        total = session.execute(counted).scalar_one()
        rows = session.execute(base.order_by(Alert.id.desc()).offset(offset).limit(limit)).scalars()
        return [_alert_view(row) for row in rows], total


def unread_count(*, actor: AuthenticatedUser) -> int:
    with unit_of_work() as uow:
        total: int = uow.session.execute(
            _mine(select(func.count()).select_from(Alert), actor).where(
                Alert.acknowledged_at.is_(None)
            )
        ).scalar_one()
        return total


def acknowledge(*, actor: AuthenticatedUser, alert_id: int) -> AlertView:
    """확인 처리 (ADR-07 "확인된 알림 재발송 금지"의 기준 시각을 찍는다).

    ★ 멱등이다 — 이미 확인한 알림을 다시 눌러도 최초 시각을 유지한다. 취소
      경로는 두지 않았다(문면 부재 — 관찰 등재).
    ★ 낙관 잠금이 없는 이유: alerts에는 version이 없고(사람이 편집하는 마스터가
      아니다), ack는 단조 1회 전이라 충돌이라는 개념이 성립하지 않는다.
    """
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(
            select(Alert).where(Alert.id == alert_id, Alert.deleted_at.is_(None))
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"alert_id": alert_id})
        if row.recipient_user_id != actor.id:
            raise ForbiddenError(log_context={"alert_id": alert_id, "actor_id": actor.id})
        if row.acknowledged_at is None:
            row.acknowledged_at = utcnow()
            row.updated_by_id = actor.id
        session.flush()
        return _alert_view(row)


# ── 알림 규칙 CRUD (관리자 전용 — 판정 요청 10) ──────────────────────────────


def _rule_view(row: AlertRule) -> AlertRuleView:
    return AlertRuleView(
        id=row.id,
        code=row.code,
        name_ko=row.name_ko,
        event_type=row.event_type,
        severity=row.severity,
        recipient_user_id=row.recipient_user_id,
        recipient_role=row.recipient_role,
        is_enabled=row.is_enabled,
        version=row.version,
    )


def _rule_body(view: AlertRuleView) -> dict[str, Any]:
    return {
        "id": view.id,
        "code": view.code,
        "name_ko": view.name_ko,
        "event_type": view.event_type,
        "severity": view.severity,
        "recipient_user_id": view.recipient_user_id,
        "recipient_role": view.recipient_role,
        "is_enabled": view.is_enabled,
        "version": view.version,
    }


def create_rule(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=RULE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        row = AlertRule(
            code=str(payload["code"]),
            name_ko=str(payload["name_ko"]),
            event_type=str(payload["event_type"]),
            severity=str(payload.get("severity") or "INFO"),
            recipient_user_id=payload.get("recipient_user_id"),
            recipient_role=payload.get("recipient_role"),
            created_by_id=actor.id,
        )
        session.add(row)
        session.flush()

        body = _rule_body(_rule_view(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_rules(*, offset: int, limit: int) -> tuple[list[AlertRuleView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(AlertRule).where(AlertRule.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(AlertRule)
            .where(AlertRule.deleted_at.is_(None))
            .order_by(AlertRule.id)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_rule_view(row) for row in rows], total


def _load_rule(session: Session, rule_id: int) -> AlertRule:
    row = session.execute(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"alert_rule_id": rule_id})
    return row


def update_rule(
    *, actor: AuthenticatedUser, rule_id: int, version: int, changes: dict[str, Any]
) -> AlertRuleView:
    with unit_of_work() as uow:
        session = uow.session
        row = _load_rule(session, rule_id)
        if row.version != version:
            raise VersionConflictError(
                log_context={"alert_rule_id": rule_id, "expected": version, "actual": row.version}
            )
        for field in ("name_ko", "event_type", "severity", "is_enabled"):
            if field in changes:
                setattr(row, field, changes[field])
        # 수신자 2필드는 3값 의미론이다: 생략=미변경 / null=해제(담당자 경로로
        # 되돌림) / 값=지정. 둘을 동시에 지정하면 명시 사용자가 이긴다.
        for field in ("recipient_user_id", "recipient_role"):
            if field in changes:
                setattr(row, field, changes[field])
        row.updated_by_id = actor.id
        session.flush()
        return _rule_view(row)


def delete_rule(*, actor: AuthenticatedUser, rule_id: int, version: int) -> None:
    with unit_of_work() as uow:
        session = uow.session
        row = _load_rule(session, rule_id)
        if row.version != version:
            raise VersionConflictError(
                log_context={"alert_rule_id": rule_id, "expected": version, "actual": row.version}
            )
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        session.flush()
