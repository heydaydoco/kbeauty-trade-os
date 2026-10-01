"""대결(위임) 서비스 — 등록·종료·목록·수임자 후보 (S3-1 ADR-0061 / design-C C5).

■ **범위 = (승인 유형 1개, 위임하는 결재 역할 1개)**. 위임자가 ADMIN이어도 수임자가 받는 권한은 그 역할 계단까지이지 ADMIN 전권이 아니다
  (권한 초과 방지). 유형을 함께 명시해 새 승인 유형이 추가돼도 기존 위임이 조용히 확장되지 않는다(fail-closed). 금액 상한 컬럼은 없다(금액 구간은
  결재선 역할 계단이 이미 표현 — 이중 정의 방지).
■ **위임자 권한 초과 금지**: 위임자는 `delegated_role`을 본인 자격으로 보유하거나 ADMIN이어야 한다 — 등록 시와 결정 시(authority) 두 번 검증한다.
  **재위임 불가**(자격은 본인 역할 보유로만 인정). 수임자는 활성·비조회 역할 보유자이고, 수임자=기안자는 **결정 시점** SoD가 건별로 막는다.
■ **기간**: KST 달력 날짜 양끝 포함. 소급 등록 금지(`start_on >= 오늘`) — 권한을 과거로 부여하는 기록은 만들지 않는다. 상한 일수 제한은 없다(관찰).
■ **유효 = 계산값**(authority.valid_delegations) — 스윕·콜백 훅이 없다. 계정 비활성·역할 상실은 즉시 무효이고 재활성되면 남은 기간이 있는 한 다시
  유효해진다(수용·문서화 — 화면이 `INERT`+사유 코드로 계산 상태를 보여 준다).
■ **중첩 금지**: 같은 (위임자, 유형, 역할)의 미종료 대결과 기간이 겹치면 409(`new.start<=old.end ∧ old.start<=new.end`, 종료된 행 제외). 직렬화는
  위임자 users 행 `FOR UPDATE` 후 서비스 검사(btree_gist 미도입 — DB EXCLUDE 불가) + 같은 시작일은 DB 부분 유니크가 최종 방어한다.
■ **조기 종료만 있고 기간 수정은 없다**(종료 후 신규 등록 — 이력 단순화). 당사자·기간·범위는 컬럼 UPDATE 권한이 DB로 불변을 강제한다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import (
    AppError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.core.time import utcnow
from app.modules.approvals.authority import NON_VIEWER_CODES, kst_today
from app.modules.approvals.machine import APPROVAL_TYPES, APPROVER_ROLES
from app.modules.approvals.models import Delegation
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import Role, RoleCode, User, UserRole
from app.modules.identity.service import AuthenticatedUser, active_roles_of

DELEGATION_CREATE_ENDPOINT = "POST /api/v1/delegations"
_START_UNIQUE = "uq_delegations_start_active"
CANDIDATE_LIMIT_MAX = 100


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def _overlap_error() -> AppError:
    return AppError(
        ErrorCode.APPROVALS_DELEGATION_OVERLAP,
        detail={"period": "같은 위임자·유형·역할의 대결 기간이 겹칩니다."},
    )


def _clean_note(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > 200:
        raise _invalid("note", "메모는 200자 이내로 입력해 주세요.")
    return text


# ── 계산 상태 ────────────────────────────────────────────────────────────────


def _user_states(session: Session, user_ids: set[int]) -> dict[int, tuple[bool, frozenset[str]]]:
    """(활성 여부, 역할 코드 집합) — 2쿼리로 일괄(목록 N+1 방지)."""
    if not user_ids:
        return {}
    active = {
        int(uid): bool(flag) and deleted is None
        for uid, flag, deleted in session.execute(
            select(User.id, User.is_active, User.deleted_at).where(User.id.in_(user_ids))
        )
    }
    roles: dict[int, set[str]] = {uid: set() for uid in active}
    for uid, code in session.execute(
        select(UserRole.user_id, Role.code)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id.in_(user_ids),
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    ):
        roles.setdefault(int(uid), set()).add(code)
    return {uid: (active[uid], frozenset(roles.get(uid, set()))) for uid in active}


def _state_of(
    row: Delegation,
    today: date,
    states: dict[int, tuple[bool, frozenset[str]]],
) -> tuple[str, str | None]:
    """(상태, INERT 사유) — ACTIVE·UPCOMING·EXPIRED·REVOKED·INERT. authority.valid_delegations의 조건과 같은 규칙이다."""
    if row.revoked_at is not None:
        return "REVOKED", None
    if row.end_on < today:
        return "EXPIRED", None
    if row.start_on > today:
        return "UPCOMING", None
    delegator_active, delegator_roles = states.get(row.delegator_user_id, (False, frozenset()))
    delegate_active, delegate_roles = states.get(row.delegate_user_id, (False, frozenset()))
    if not delegator_active:
        return "INERT", "delegator_inactive"
    if not delegate_active:
        return "INERT", "delegate_inactive"
    if row.delegated_role not in delegator_roles and RoleCode.ADMIN.value not in delegator_roles:
        return "INERT", "delegator_lost_role"
    if not (delegate_roles & set(NON_VIEWER_CODES)):
        return "INERT", "delegate_no_role"
    return "ACTIVE", None


def _body(
    row: Delegation,
    *,
    actor: AuthenticatedUser,
    today: date,
    names: dict[int, str],
    states: dict[int, tuple[bool, frozenset[str]]],
) -> dict[str, Any]:
    state, inert_reason = _state_of(row, today, states)
    can_revoke = state in {"ACTIVE", "UPCOMING", "INERT"} and (
        row.delegator_user_id == actor.id or RoleCode.ADMIN in actor.roles
    )
    return {
        "id": row.id,
        "delegator_user_id": row.delegator_user_id,
        "delegator_name": names.get(row.delegator_user_id),
        "delegate_user_id": row.delegate_user_id,
        "delegate_name": names.get(row.delegate_user_id),
        "approval_type": row.approval_type,
        "delegated_role": row.delegated_role,
        "start_on": row.start_on.isoformat(),
        "end_on": row.end_on.isoformat(),
        "note": row.note,
        "state": state,
        "inert_reason": inert_reason,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "can_revoke": can_revoke,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
    }


def _bodies(
    session: Session, rows: list[Delegation], *, actor: AuthenticatedUser
) -> list[dict[str, Any]]:
    ids = {r.delegator_user_id for r in rows} | {r.delegate_user_id for r in rows}
    names = (
        {
            int(uid): name
            for uid, name in session.execute(
                select(User.id, User.display_name).where(User.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    states = _user_states(session, ids)
    today = kst_today()
    return [_body(r, actor=actor, today=today, names=names, states=states) for r in rows]


# ── 등록 ─────────────────────────────────────────────────────────────────────


def create_delegation(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=DELEGATION_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        approval_type = str(payload["approval_type"])
        role = str(payload["delegated_role"])
        if approval_type not in APPROVAL_TYPES:
            raise _invalid("approval_type", "알 수 없는 승인 유형입니다.")
        if role not in APPROVER_ROLES:
            raise _invalid("delegated_role", "알 수 없는 결재 역할입니다.")
        start_on: date = payload["start_on"]
        end_on: date = payload["end_on"]
        if end_on < start_on:
            raise _invalid("end_on", "종료일은 시작일 이후여야 합니다.")
        if start_on < kst_today():
            raise _invalid(
                "start_on",
                "과거 날짜로는 대결을 등록할 수 없습니다. 시작일을 오늘 이후로 입력해 주세요.",
            )

        delegator_id = payload.get("delegator_user_id")
        if delegator_id is None:
            delegator_id = actor.id
        elif delegator_id != actor.id and RoleCode.ADMIN not in actor.roles:
            raise ForbiddenError(log_context={"actor_id": actor.id, "op": "delegate_for_other"})
        delegate_id = int(payload["delegate_user_id"])
        if delegate_id == delegator_id:
            raise _invalid("delegate_user_id", "본인에게 대결을 지정할 수 없습니다.")

        # 위임자 행을 먼저 잠근다 — 같은 위임자의 동시 등록(겹침 검사)을 직렬화한다.
        locked = session.execute(
            select(User.id)
            .where(User.id == delegator_id, User.deleted_at.is_(None))
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise _invalid("delegator_user_id", "존재하지 않는 사용자입니다.")
        delegator_roles = active_roles_of(session, delegator_id)
        if delegator_roles is None:
            raise _invalid("delegator_user_id", "비활성 계정은 대결을 위임할 수 없습니다.")
        if role not in {r.value for r in delegator_roles} and RoleCode.ADMIN not in delegator_roles:
            raise _invalid(
                "delegated_role",
                "위임자가 보유하지 않은 역할은 위임할 수 없습니다. 위임자의 결재 역할을 확인해 주세요.",
            )
        delegate_roles = active_roles_of(session, delegate_id)
        if delegate_roles is None:
            raise _invalid("delegate_user_id", "존재하지 않거나 비활성인 사용자입니다.")
        if not any(r.value in NON_VIEWER_CODES for r in delegate_roles):
            raise _invalid(
                "delegate_user_id",
                "조회 전용 사용자는 수임자가 될 수 없습니다. 결재 역할이 있는 사용자를 선택해 주세요.",
            )

        overlapping = session.execute(
            select(Delegation.id).where(
                Delegation.delegator_user_id == delegator_id,
                Delegation.approval_type == approval_type,
                Delegation.delegated_role == role,
                Delegation.revoked_at.is_(None),
                Delegation.start_on <= end_on,
                Delegation.end_on >= start_on,
            )
        ).first()
        if overlapping is not None:
            raise _overlap_error()

        row = Delegation(
            delegator_user_id=delegator_id,
            delegate_user_id=delegate_id,
            approval_type=approval_type,
            delegated_role=role,
            start_on=start_on,
            end_on=end_on,
            note=_clean_note(payload.get("note")),
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            if _START_UNIQUE in str(exc.orig or ""):
                raise _overlap_error() from exc
            raise
        audit.record(
            session,
            action=AuditAction.DELEGATION_CREATED,
            actor_user_id=actor.id,
            entity_type="delegations",
            entity_id=row.id,
            detail={
                "delegator_user_id": delegator_id,
                "delegate_user_id": delegate_id,
                "approval_type": approval_type,
                "delegated_role": role,
                "start_on": start_on.isoformat(),
                "end_on": end_on.isoformat(),
            },
        )
        body = _bodies(session, [row], actor=actor)[0]
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


# ── 종료 ─────────────────────────────────────────────────────────────────────


def revoke_delegation(
    *, actor: AuthenticatedUser, delegation_id: int, version: int
) -> dict[str, Any]:
    """조기 종료 — 위임자 본인 또는 ADMIN. 이미 종료됐거나 기간이 지난 건은 409."""
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(
            select(Delegation)
            .where(Delegation.id == delegation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"delegation_id": delegation_id})
        if row.delegator_user_id != actor.id and RoleCode.ADMIN not in actor.roles:
            raise ForbiddenError(log_context={"delegation_id": delegation_id, "actor_id": actor.id})
        if row.version != version:
            raise VersionConflictError(log_context={"delegation_id": delegation_id})
        if row.revoked_at is not None or row.end_on < kst_today():
            raise AppError(ErrorCode.APPROVALS_DELEGATION_NOT_ACTIVE)
        row.revoked_at = utcnow()
        row.revoked_by_id = actor.id
        row.updated_by_id = actor.id
        session.flush()
        audit.record(
            session,
            action=AuditAction.DELEGATION_REVOKED,
            actor_user_id=actor.id,
            entity_type="delegations",
            entity_id=row.id,
            detail={
                "delegator_user_id": row.delegator_user_id,
                "delegate_user_id": row.delegate_user_id,
                "approval_type": row.approval_type,
                "delegated_role": row.delegated_role,
            },
        )
        return _bodies(session, [row], actor=actor)[0]


# ── 조회 ─────────────────────────────────────────────────────────────────────


def list_delegations(
    *, actor: AuthenticatedUser, scope: str, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """`mine`(내가 위임했거나 내가 수임한 건) / `all`(ADMIN 전용)."""
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[Any] = []
        if scope == "all":
            if RoleCode.ADMIN not in actor.roles:
                raise ForbiddenError(log_context={"scope": "all", "actor_id": actor.id})
        else:
            conditions.append(
                or_(
                    Delegation.delegator_user_id == actor.id,
                    Delegation.delegate_user_id == actor.id,
                )
            )
        total = session.execute(
            select(func.count()).select_from(Delegation).where(*conditions)
        ).scalar_one()
        rows = list(
            session.execute(
                select(Delegation)
                .where(*conditions)
                .order_by(Delegation.end_on.desc(), Delegation.id.desc())
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        return _bodies(session, rows, actor=actor), int(total)


def list_candidates(
    *, actor: AuthenticatedUser, q: str | None, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """수임자 후보 — 활성·비조회 역할 보유자(본인 제외). **`{id, display_name}` 두 키뿐**(이메일·역할·사유 미노출)."""
    limit = min(limit, CANDIDATE_LIMIT_MAX)
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[Any] = [
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.id != actor.id,
            User.id.in_(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(
                    UserRole.deleted_at.is_(None),
                    Role.deleted_at.is_(None),
                    Role.code.in_(NON_VIEWER_CODES),
                )
            ),
        ]
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(User.display_name.ilike(f"%{escaped}%", escape="\\"))
        total = session.execute(
            select(func.count()).select_from(User).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(User.id, User.display_name)
            .where(*conditions)
            .order_by(User.display_name, User.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [{"id": int(r[0]), "display_name": r[1]} for r in rows], int(total)
