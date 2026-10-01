"""승인 읽기 쪽 — 결재함·내 요청·상세·이력·배지 (S3-1 ADR-0061 / design-C C7·C8).

■ **결재함 술어는 `authority.eligibility_clause`** — 결재 판정(`decision_authority`)과 같은 규칙·같은 SQL 조각(대결)이다. 알림과
  무관하게 서버가 계산한다("알림이 없거나 늦어도 결재함에는 뜬다"). 기안자 본인 행은 결재함에 나오지 않는다(ADMIN 포함).
■ **프런트가 `hasRole`로 `can_decide`를 추정하지 않는다** — ADMIN은 `hasRole`이 늘 true라 "기안자=승인자 버튼"이 상시 노출된다.
  `can_decide`·`can_withdraw`·`decide_blocked_reason`은 서버가 계산해 응답에 싣는다.
■ **IDOR**: 상세·이력은 기안자·결정자(대결 포함)·현재 결재 자격자·ADMIN만 본다(그 외 403). 목록은 범위(scope)가 곧 접근 통제다.
■ 목록은 상수 쿼리(승인 1 + 사용자 이름 1 + 최신 이벤트 1) — 건수에 비례하는 N+1이 없다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import ForbiddenError, NotFoundError
from app.modules.approvals.authority import (
    Authority,
    decision_authority,
    eligibility_clause,
    kst_today,
)
from app.modules.approvals.machine import ACTIVE_STATUSES, ApprovalStatus, AuthorityKind
from app.modules.approvals.models import Approval, ApprovalEvent
from app.modules.identity.models import RoleCode, User
from app.modules.identity.service import AuthenticatedUser, active_roles_of

Scope = Literal["inbox", "mine", "all"]


def _names(session: Session, user_ids: set[int] | set[int | None]) -> dict[int, str]:
    wanted = {uid for uid in user_ids if uid is not None}
    if not wanted:
        return {}
    return {
        int(uid): name
        for uid, name in session.execute(
            select(User.id, User.display_name).where(User.id.in_(wanted))
        )
    }


def _latest_events(session: Session, approval_ids: list[int]) -> dict[int, ApprovalEvent]:
    """승인별 최신 이벤트 1건 — 한 쿼리(DISTINCT ON)."""
    if not approval_ids:
        return {}
    rows = session.execute(
        select(ApprovalEvent)
        .where(ApprovalEvent.approval_id.in_(approval_ids))
        .distinct(ApprovalEvent.approval_id)
        .order_by(ApprovalEvent.approval_id, ApprovalEvent.id.desc())
    ).scalars()
    return {row.approval_id: row for row in rows}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _can_withdraw(approval: Approval, actor_id: int, roles: frozenset[RoleCode]) -> bool:
    """회수 가능 — 활성(요청됨·승인됨) 승인을 기안자 본인 또는 ADMIN이."""
    if approval.status not in {s.value for s in ACTIVE_STATUSES}:
        return False
    return approval.requested_by_id == actor_id or RoleCode.ADMIN in roles


def approval_body(
    session: Session,
    approval: Approval,
    *,
    actor: AuthenticatedUser,
    today: date | None = None,
    names: dict[int, str] | None = None,
    event: ApprovalEvent | None = None,
    authority: Authority | None = None,
) -> dict[str, Any]:
    """승인 1건의 응답 본문 — 서버 계산 필드(`can_decide`·`can_withdraw`·`decide_blocked_reason`) 포함."""
    today = today or kst_today()
    roles = active_roles_of(session, actor.id) or frozenset()
    names = names or _names(
        session,
        {approval.requested_by_id, approval.decided_by_id, approval.decided_on_behalf_of_id},
    )
    if event is None:
        event = _latest_events(session, [approval.id]).get(approval.id)
    can_decide = False
    blocked: str | None = None
    if approval.status == ApprovalStatus.REQUESTED.value:
        authority = authority or decision_authority(
            session, actor_id=actor.id, approval=approval, today=today
        )
        can_decide = authority.can_decide
        if not can_decide:
            blocked = "SELF" if authority.kind is AuthorityKind.DENIED_SELF else "NOT_APPROVER"
    status_reason: str | None = None
    void_reason_code: str | None = None
    if event is not None and approval.status in {
        ApprovalStatus.REJECTED.value,
        ApprovalStatus.WITHDRAWN.value,
        ApprovalStatus.VOIDED.value,
    }:
        status_reason = event.reason
        void_reason_code = event.reason_code
    return {
        "id": approval.id,
        "approval_type": approval.approval_type,
        "target_type": approval.target_type,
        "target_id": approval.target_id,
        "target_label": approval.target_label,
        "status": approval.status,
        "required_role": approval.required_role,
        "requested_by_id": approval.requested_by_id,
        "requester_name": names.get(approval.requested_by_id),
        "basis_amount": approval.basis_amount,
        "basis_currency": approval.basis_currency,
        "snapshot": approval.snapshot,
        "decided_by_name": names.get(approval.decided_by_id) if approval.decided_by_id else None,
        "decided_on_behalf_of_name": (
            names.get(approval.decided_on_behalf_of_id)
            if approval.decided_on_behalf_of_id
            else None
        ),
        "decided_at": _iso(approval.decided_at),
        "consumed_at": _iso(approval.consumed_at),
        "status_reason": status_reason,
        "void_reason_code": void_reason_code,
        "can_decide": can_decide,
        "decide_blocked_reason": blocked,
        "can_withdraw": _can_withdraw(approval, actor.id, roles),
        "created_at": _iso(approval.created_at),
        "version": approval.version,
    }


def _may_view(
    session: Session, approval: Approval, actor: AuthenticatedUser, roles: frozenset[RoleCode]
) -> bool:
    """상세·이력 열람 자격 — 기안자·결정자·결재 자격자(대결 포함)·ADMIN. VIEWER 단독은 라우터 게이트가 막는다."""
    if RoleCode.ADMIN in roles or actor.id == approval.requested_by_id:
        return True
    if actor.id in (approval.decided_by_id, approval.decided_on_behalf_of_id):
        return True
    eligible = session.execute(
        select(Approval.id).where(
            Approval.id == approval.id,
            eligibility_clause(actor_id=actor.id, roles=roles, today=kst_today()),
        )
    ).scalar_one_or_none()
    return eligible is not None


def get_approval(*, actor: AuthenticatedUser, approval_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        session = uow.session
        approval = session.get(Approval, approval_id)
        if approval is None:
            raise NotFoundError(log_context={"approval_id": approval_id})
        roles = active_roles_of(session, actor.id) or frozenset()
        if not _may_view(session, approval, actor, roles):
            raise ForbiddenError(log_context={"approval_id": approval_id, "actor_id": actor.id})
        return approval_body(session, approval, actor=actor)


def list_events(
    *, actor: AuthenticatedUser, approval_id: int, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    with unit_of_work() as uow:
        session = uow.session
        approval = session.get(Approval, approval_id)
        if approval is None:
            raise NotFoundError(log_context={"approval_id": approval_id})
        roles = active_roles_of(session, actor.id) or frozenset()
        if not _may_view(session, approval, actor, roles):
            raise ForbiddenError(log_context={"approval_id": approval_id, "actor_id": actor.id})
        total = session.execute(
            select(func.count())
            .select_from(ApprovalEvent)
            .where(ApprovalEvent.approval_id == approval_id)
        ).scalar_one()
        rows = list(
            session.execute(
                select(ApprovalEvent)
                .where(ApprovalEvent.approval_id == approval_id)
                .order_by(ApprovalEvent.id)
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        names = _names(
            session,
            {r.actor_user_id for r in rows} | {r.on_behalf_of_user_id for r in rows},
        )
        return (
            [
                {
                    "id": r.id,
                    "occurred_at": r.occurred_at.isoformat(),
                    "from_status": r.from_status,
                    "to_status": r.to_status,
                    "actor_name": names.get(r.actor_user_id),
                    "on_behalf_of_name": (
                        names.get(r.on_behalf_of_user_id) if r.on_behalf_of_user_id else None
                    ),
                    "reason": r.reason,
                    "reason_code": r.reason_code,
                }
                for r in rows
            ],
            int(total),
        )


def list_approvals(
    *,
    actor: AuthenticatedUser,
    scope: Scope,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """범위별 목록 — inbox(결정 가능한 REQUESTED, 오래된 순) · mine(본인 기안) · all(ADMIN 전용)."""
    with unit_of_work() as uow:
        session = uow.session
        roles = active_roles_of(session, actor.id) or frozenset()
        today = kst_today()
        conditions: list[Any] = []
        order: list[Any] = [Approval.id.desc()]
        if scope == "inbox":
            conditions.append(Approval.status == ApprovalStatus.REQUESTED.value)
            conditions.append(eligibility_clause(actor_id=actor.id, roles=roles, today=today))
            order = [Approval.id]  # 요청 오래된 순 — 정체 건이 위로
        elif scope == "mine":
            conditions.append(Approval.requested_by_id == actor.id)
        else:
            if RoleCode.ADMIN not in roles:
                raise ForbiddenError(log_context={"scope": "all", "actor_id": actor.id})
        if status:
            conditions.append(Approval.status == status)
        total = session.execute(
            select(func.count()).select_from(Approval).where(*conditions)
        ).scalar_one()
        rows = list(
            session.execute(
                select(Approval).where(*conditions).order_by(*order).offset(offset).limit(limit)
            ).scalars()
        )
        names = _names(
            session,
            {r.requested_by_id for r in rows}
            | {r.decided_by_id for r in rows}
            | {r.decided_on_behalf_of_id for r in rows},
        )
        events = _latest_events(session, [r.id for r in rows])
        return (
            [
                approval_body(
                    session, r, actor=actor, today=today, names=names, event=events.get(r.id)
                )
                for r in rows
            ],
            int(total),
        )


def inbox_count(*, actor: AuthenticatedUser) -> int:
    """셸 배지 — 내가 결정할 수 있는 결재 대기 건수(알림과 무관한 서버 계산)."""
    with unit_of_work() as uow:
        session = uow.session
        roles = active_roles_of(session, actor.id) or frozenset()
        return int(
            session.execute(
                select(func.count())
                .select_from(Approval)
                .where(
                    Approval.status == ApprovalStatus.REQUESTED.value,
                    eligibility_clause(actor_id=actor.id, roles=roles, today=kst_today()),
                )
            ).scalar_one()
        )
