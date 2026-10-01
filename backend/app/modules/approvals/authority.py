"""결재 자격·직무분리(SoD)·대결 유효성 — **단일 정의** (S3-1 ADR-0061 / design-C C4·C5).

"누가 이 승인을 결정할 수 있는가"의 답은 이 파일 하나에서 나온다. 결재함(SQL)·`can_decide`(화면 필드)·결정 API(서비스 판정)·
알림 수신자·요청 시 자격자 공집합 검사가 **같은 SQL 조각**(`valid_delegations`)과 같은 규칙을 공유한다 — Python/SQL 이중 정의를
만들면 "결재함엔 보이는데 결정은 403" 같은 어긋남이 조용히 생긴다(동등성 테스트가 무작위 조합으로 고정한다).

■ 자격 = 역할 기반(지정 사용자 컬럼 없음). 결정 시점 자격자 =
    ① 활성 계정 ∧ `required_role` 보유자
    ② **활성 ADMIN 전원**(모든 유형·구간의 자격자 — 결재선 0행·역할 보유자 0명이어도 결재가 영구 정체하지 않게)
    ③ `required_role`·유형에 대해 **오늘(KST) 유효한 대결 수임자**
  모두 **기안자 본인 제외**.
■ **직무분리: 기안자 ≠ 결정자, 예외 없음 — ADMIN도 자기 기안을 승인·반려할 수 없다.** 대결 경유는 수임자≠기안자 ∧ 위임자≠기안자.
  §2 "관리자는 상시 통과"는 역할 가드(엔드포인트 진입)이고 §20 H "승인 우회 차단"은 업무 게이트다 — 층이 달라 충돌하지 않는다.
■ 대결 유효 = 조회·결정 시점 **계산값**(스윕·콜백 훅 없음): 미종료 ∧ 시작일 ≤ 오늘(KST) ≤ 종료일 ∧ 위임자·수임자 활성 ∧ 위임자가 지금도
  그 역할(또는 ADMIN)을 보유 ∧ 수임자가 비조회 역할 보유. 계정 비활성·역할 상실은 즉시 무효다(fail-closed). **재위임 불가**: 자격은
  "위임받은 권한"이 아니라 본인 역할 보유로만 인정하므로 A→B→C 사슬은 구조적으로 성립하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, exists, false, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.core.time import today_kst
from app.modules.approvals.machine import APPROVER_ROLES, AuthorityKind
from app.modules.approvals.models import Approval, Delegation
from app.modules.identity.models import Role, RoleCode, User, UserRole
from app.modules.identity.service import active_roles_of

#: 결재 자격이 있는 역할(= VIEWER 제외 4종).
NON_VIEWER_CODES: tuple[str, ...] = APPROVER_ROLES


def kst_today() -> date:
    """결재·대결 판정의 "오늘"(KST 달력 날짜) — **단일 진입점**. 테스트는 이 함수를 monkeypatch해 날짜를 고정한다."""
    return today_kst()


@dataclass(frozen=True, slots=True)
class Authority:
    kind: AuthorityKind
    #: 대결 경유일 때만 — 사용된 대결 id와 위임자.
    delegation_id: int | None = None
    on_behalf_of_id: int | None = None

    @property
    def can_decide(self) -> bool:
        return self.kind in (AuthorityKind.OWN, AuthorityKind.ADMIN, AuthorityKind.DELEGATED)


def valid_delegations(
    *,
    approval_type: Any,
    required_role: Any,
    requested_by_id: Any,
    today: date,
    select_columns: tuple[Any, ...] | None = None,
) -> Select[Any]:
    """오늘 유효한 대결 행 선택문 — 인자는 승인 행의 **컬럼 식이거나 파이썬 값**이다(같은 조각을 두 곳이 쓴다).

    위임자: 활성 ∧ 지금도 delegated_role(또는 ADMIN) 보유 ∧ 기안자가 아님.
    수임자: 활성 ∧ 비조회 역할 1개 이상 보유 ∧ 기안자가 아님.
    """
    delegator = aliased(User)
    delegate = aliased(User)
    delegator_role_link = aliased(UserRole)
    delegator_role = aliased(Role)
    delegate_role_link = aliased(UserRole)
    delegate_role = aliased(Role)

    delegator_holds_role = exists().where(
        delegator_role_link.user_id == Delegation.delegator_user_id,
        delegator_role_link.deleted_at.is_(None),
        delegator_role.id == delegator_role_link.role_id,
        delegator_role.deleted_at.is_(None),
        delegator_role.code.in_([Delegation.delegated_role, RoleCode.ADMIN.value]),
    )
    delegate_holds_a_role = exists().where(
        delegate_role_link.user_id == Delegation.delegate_user_id,
        delegate_role_link.deleted_at.is_(None),
        delegate_role.id == delegate_role_link.role_id,
        delegate_role.deleted_at.is_(None),
        delegate_role.code.in_(NON_VIEWER_CODES),
    )
    columns = select_columns or (Delegation.id,)
    return (
        select(*columns)
        .select_from(Delegation)
        .join(delegator, delegator.id == Delegation.delegator_user_id)
        .join(delegate, delegate.id == Delegation.delegate_user_id)
        .where(
            Delegation.approval_type == approval_type,
            Delegation.delegated_role == required_role,
            Delegation.revoked_at.is_(None),
            Delegation.start_on <= today,
            Delegation.end_on >= today,
            Delegation.delegator_user_id != requested_by_id,
            Delegation.delegate_user_id != requested_by_id,
            delegator.deleted_at.is_(None),
            delegator.is_active.is_(True),
            delegate.deleted_at.is_(None),
            delegate.is_active.is_(True),
            delegator_holds_role,
            delegate_holds_a_role,
        )
    )


def eligibility_clause(
    *, actor_id: int, roles: frozenset[RoleCode], today: date
) -> ColumnElement[bool]:
    """결재함 술어 — 이 사용자가 **결정할 수 있는** 승인 행(Approval 컬럼 기준). 기안자 본인 행은 항상 제외된다."""
    non_viewer = {role.value for role in roles if role is not RoleCode.VIEWER}
    if not non_viewer:
        return false()
    if RoleCode.ADMIN in roles:
        role_ok: ColumnElement[bool] = true()
    else:
        role_ok = Approval.required_role.in_(sorted(non_viewer))
    delegated = exists(
        valid_delegations(
            approval_type=Approval.approval_type,
            required_role=Approval.required_role,
            requested_by_id=Approval.requested_by_id,
            today=today,
        ).where(Delegation.delegate_user_id == actor_id)
    )
    return and_(Approval.requested_by_id != actor_id, or_(role_ok, delegated))


def find_delegation(
    session: Session, *, actor_id: int, approval: Approval, today: date
) -> tuple[int, int] | None:
    """이 승인에 대해 actor가 쓸 수 있는 유효 대결 — (대결 id, 위임자 id). 여럿이면 id 최소(결정적)."""
    row = session.execute(
        valid_delegations(
            approval_type=approval.approval_type,
            required_role=approval.required_role,
            requested_by_id=approval.requested_by_id,
            today=today,
            select_columns=(Delegation.id, Delegation.delegator_user_id),
        )
        .where(Delegation.delegate_user_id == actor_id)
        .order_by(Delegation.id)
        .limit(1)
    ).first()
    return None if row is None else (int(row[0]), int(row[1]))


def decision_authority(
    session: Session, *, actor_id: int, approval: Approval, today: date | None = None
) -> Authority:
    """이 사용자가 이 승인을 결정할 수 있는가 — **서비스 판정의 유일한 진입점**.

    역할은 `AuthenticatedUser`의 값을 믿지 않고 **지금 DB에서 다시 읽는다**(승인 행 잠금 뒤 호출 — 목록에서 보였던 사람이 그 사이
    비활성화·역할 회수됐을 수 있다). 기안자 본인은 어떤 경로로도 결정자가 될 수 없다(ADMIN 포함).
    """
    today = today or kst_today()
    if approval.requested_by_id == actor_id:
        return Authority(AuthorityKind.DENIED_SELF)
    roles = active_roles_of(session, actor_id)
    if roles is None or not any(role.value in NON_VIEWER_CODES for role in roles):
        return Authority(AuthorityKind.DENIED_NOT_APPROVER)
    if approval.required_role in {role.value for role in roles}:
        return Authority(AuthorityKind.OWN)
    if RoleCode.ADMIN in roles:
        return Authority(AuthorityKind.ADMIN)
    found = find_delegation(session, actor_id=actor_id, approval=approval, today=today)
    if found is not None:
        return Authority(AuthorityKind.DELEGATED, delegation_id=found[0], on_behalf_of_id=found[1])
    return Authority(AuthorityKind.DENIED_NOT_APPROVER)


def _role_holder_ids(session: Session, codes: list[str], *, exclude_user_id: int) -> set[int]:
    return set(
        session.execute(
            select(User.id)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                Role.code.in_(codes),
                Role.deleted_at.is_(None),
                UserRole.deleted_at.is_(None),
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.id != exclude_user_id,
            )
        ).scalars()
    )


def _delegate_ids(
    session: Session, *, approval_type: str, required_role: str, requested_by_id: int, today: date
) -> set[int]:
    return set(
        session.execute(
            valid_delegations(
                approval_type=approval_type,
                required_role=required_role,
                requested_by_id=requested_by_id,
                today=today,
                select_columns=(Delegation.delegate_user_id,),
            )
        ).scalars()
    )


def eligible_user_ids(
    session: Session, approval: Approval, *, today: date | None = None
) -> list[int]:
    """결정 **자격자 전원** — 역할 보유자 ∪ 활성 ADMIN ∪ 유효 수임자, 기안자 제외(요청 시 공집합 검사용)."""
    today = today or kst_today()
    direct = _role_holder_ids(
        session,
        [approval.required_role, RoleCode.ADMIN.value],
        exclude_user_id=approval.requested_by_id,
    )
    delegated = _delegate_ids(
        session,
        approval_type=approval.approval_type,
        required_role=approval.required_role,
        requested_by_id=approval.requested_by_id,
        today=today,
    )
    return sorted(direct | delegated)


def notification_recipients(
    session: Session, approval: Approval, *, today: date | None = None
) -> tuple[list[int], bool]:
    """요청·독촉 알림 수신자 — `(user_ids, fallback)`.

    ① `required_role` 보유 활성 사용자 ∪ 오늘 유효한 그 (유형·역할) 수임자 − 기안자(−위임자=기안자인 수임자)
    ② ①이 공집합이면 **활성 ADMIN 전원(기안자 제외)** 폴백 — 본문에 안내 문구가 붙는다.
    ADMIN은 역할 보유자가 있으면 수신 제외다(전사 폭포 금지 — ADR-07) — 단 결재함에는 항상 보인다.
    """
    today = today or kst_today()
    holders = _role_holder_ids(
        session, [approval.required_role], exclude_user_id=approval.requested_by_id
    )
    delegated = _delegate_ids(
        session,
        approval_type=approval.approval_type,
        required_role=approval.required_role,
        requested_by_id=approval.requested_by_id,
        today=today,
    )
    primary = sorted(holders | delegated)
    if primary:
        return primary, False
    admins = sorted(
        _role_holder_ids(session, [RoleCode.ADMIN.value], exclude_user_id=approval.requested_by_id)
    )
    return admins, True
