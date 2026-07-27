"""로그인·세션 (DESIGN.md §18.1 / §17.1 / ADR-0013).

★ 실패한 로그인도 **커밋되어야 한다**.
  실패 카운터 증가와 잠금은 "실패했다"는 사실의 기록이다. 예외를 먼저 던지면
  unit_of_work가 전부 롤백해 카운터가 영원히 0에 머무르고, 5회 잠금은 조용히
  작동하지 않는 기능이 된다. 그래서 이 모듈은 **트랜잭션 안에서 판정만 하고,
  커밋이 끝난 뒤에 예외를 던진다**.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.time import utcnow
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.identity.models import Role, RoleCode, User, UserRole, UserSession
from app.modules.identity.passwords import DUMMY_HASH, verify_password

#: 브라우저에 내려가는 쿠키 이름 (ADR-0013).
SESSION_COOKIE_NAME = "kbos_session"

#: §18.1 "로그인 연속 실패 잠금(5회)". 설계서가 정한 값이라 설정으로 빼지 않는다 —
#: 설정이 되는 순간 누군가 운영에서 100으로 올려도 아무도 모른다.
MAX_FAILED_LOGINS = 5

#: 잠금 지속 시간. 설계서에 수치가 없어 여기서 정한다. 영구 잠금으로 하지 않는
#: 이유는 관리자 개입 없이는 아무도 못 들어오는 상태가 실무에서 더 위험하기 때문.
LOCKOUT_DURATION = timedelta(minutes=15)

#: 세션 수명(절대 만료). 하루 근무를 덮되 퇴근 후에는 끊긴다.
SESSION_LIFETIME = timedelta(hours=12)

#: last_seen_at을 매 요청 쓰면 조회에도 쓰기 트랜잭션이 붙는다. 이 간격보다
#: 오래됐을 때만 갱신한다.
LAST_SEEN_REFRESH = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """요청을 수행 중인 사람. 라우터·서비스는 이 값만 본다(ORM 객체를 넘기지 않는다)."""

    id: int
    email: str
    display_name: str
    roles: frozenset[RoleCode]
    session_id: int

    def has_any_role(self, *codes: RoleCode) -> bool:
        return bool(self.roles & set(codes))


def hash_session_token(token: str) -> str:
    """쿠키의 원문 토큰 → DB에 저장하는 조회 키."""
    return sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _load_roles(session: Session, user_id: int) -> frozenset[RoleCode]:
    codes = session.execute(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user_id,
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    ).scalars()
    return frozenset(RoleCode(code) for code in codes)


def login(
    *, email: str, password: str, existing_token: str | None = None
) -> tuple[str, AuthenticatedUser]:
    """자격 증명을 검사하고 새 세션을 발급한다.

    existing_token — 요청에 실려 온 기존 세션 쿠키. 있으면 **폐기한다**.
    로그인 성공이 언제나 새 토큰을 만들어야 세션 고정 공격이 성립하지 않는다
    (공격자가 심어 둔 세션ID가 인증된 세션으로 승격되는 경로를 끊는다).
    """
    normalized = normalize_email(email)
    failure: ErrorCode | None = None
    token = ""
    principal: AuthenticatedUser | None = None

    with unit_of_work() as uow:
        session = uow.session
        now = utcnow()
        user = session.execute(
            select(User)
            .where(User.email == normalized, User.deleted_at.is_(None))
            # 같은 계정에 동시 시도가 들어와도 실패 카운터가 정확히 5에서 잠기도록
            # 직렬화한다(§17.2 "확인→기록"이 깨지는 지점만 행 잠금).
            .with_for_update()
        ).scalar_one_or_none()

        if user is None:
            verify_password(DUMMY_HASH, password)  # 응답 시간 평탄화
            audit.record(
                session,
                action=AuditAction.LOGIN_FAILED,
                detail={"email": normalized, "reason": "unknown_email"},
            )
            failure = ErrorCode.AUTH_INVALID_CREDENTIALS

        elif user.locked_until is not None and user.locked_until > now:
            audit.record(
                session,
                action=AuditAction.LOGIN_BLOCKED,
                actor_user_id=user.id,
                detail={"reason": "locked", "locked_until": user.locked_until.isoformat()},
            )
            failure = ErrorCode.AUTH_ACCOUNT_LOCKED

        elif not user.is_active:
            audit.record(
                session,
                action=AuditAction.LOGIN_BLOCKED,
                actor_user_id=user.id,
                detail={"reason": "inactive"},
            )
            failure = ErrorCode.AUTH_ACCOUNT_INACTIVE

        elif not verify_password(user.password_hash, password):
            user.failed_login_count += 1
            locked = user.failed_login_count >= MAX_FAILED_LOGINS
            if locked:
                user.locked_until = now + LOCKOUT_DURATION
            audit.record(
                session,
                action=AuditAction.LOGIN_FAILED,
                actor_user_id=user.id,
                detail={
                    "reason": "bad_password",
                    "failed_login_count": user.failed_login_count,
                    "locked": locked,
                },
            )
            failure = (
                ErrorCode.AUTH_ACCOUNT_LOCKED if locked else ErrorCode.AUTH_INVALID_CREDENTIALS
            )

        else:
            user.failed_login_count = 0
            user.locked_until = None
            user.last_login_at = now

            if existing_token:
                _revoke_by_token(session, existing_token, now)
            _purge_expired_sessions(session, user.id, now)

            token, session_row = _issue_session(session, user.id, now)
            principal = AuthenticatedUser(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                roles=_load_roles(session, user.id),
                session_id=session_row.id,
            )
            audit.record(
                session,
                action=AuditAction.LOGIN_SUCCEEDED,
                actor_user_id=user.id,
                entity_type="user_sessions",
                entity_id=session_row.id,
            )

    # ── 여기부터는 커밋이 끝난 뒤다. 실패 기록은 이미 장부에 남았다. ──
    if failure is not None:
        raise AppError(failure)
    assert principal is not None  # 실패가 없으면 반드시 채워져 있다
    return token, principal


def resolve_session(token: str) -> AuthenticatedUser | None:
    """쿠키 토큰 → 사용자. 유효하지 않으면 None(사유는 밝히지 않는다)."""
    now = utcnow()
    with unit_of_work() as uow:
        session = uow.session
        found = session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.token_hash == hash_session_token(token),
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
            )
        ).first()
        if found is None:
            return None
        session_row, user = found
        if session_row.last_seen_at < now - LAST_SEEN_REFRESH:
            session_row.last_seen_at = now
        return AuthenticatedUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            roles=_load_roles(session, user.id),
            session_id=session_row.id,
        )


def logout(token: str, *, actor_user_id: int | None = None) -> None:
    """세션을 폐기한다. 이미 없는 세션이어도 조용히 성공한다(멱등)."""
    now = utcnow()
    with unit_of_work() as uow:
        session = uow.session
        revoked_id = _revoke_by_token(session, token, now)
        if revoked_id is not None:
            audit.record(
                session,
                action=AuditAction.LOGOUT,
                actor_user_id=actor_user_id,
                entity_type="user_sessions",
                entity_id=revoked_id,
            )


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────────


def _issue_session(session: Session, user_id: int, now: datetime) -> tuple[str, UserSession]:
    token = token_urlsafe(32)
    row = UserSession(
        user_id=user_id,
        token_hash=hash_session_token(token),
        expires_at=now + SESSION_LIFETIME,
        last_seen_at=now,
    )
    session.add(row)
    session.flush()
    return token, row


def _revoke_by_token(session: Session, token: str, now: datetime) -> int | None:
    row = session.execute(
        select(UserSession).where(
            UserSession.token_hash == hash_session_token(token),
            UserSession.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.revoked_at = now
    return row.id


def _purge_expired_sessions(session: Session, user_id: int, now: datetime) -> None:
    """만료된 세션 행을 지운다 (ADR-0013 "만료 세션 정리").

    로그인 시점에 해당 사용자분만 치운다 — 전역 청소는 배치의 일이고,
    로그인 경로에서 전체 테이블을 훑으면 사용자가 그 비용을 기다린다.
    """
    session.execute(
        delete(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.expires_at <= now,
        )
    )


# ── 사용자 조회·역할 부여 (§2 권한·통제 / §18.1 인가·audit) ─────────────────


@dataclass(frozen=True, slots=True)
class UserView:
    """읽기 전용 사용자 표현. ORM 객체를 트랜잭션 밖으로 내보내지 않는다."""

    id: int
    email: str
    display_name: str
    is_active: bool
    roles: frozenset[RoleCode]


def _roles_by_user(session: Session, user_ids: list[int]) -> dict[int, set[RoleCode]]:
    """페이지 전체의 역할을 한 번에 읽는다.

    사용자마다 역할을 따로 조회하면 목록 50건에 쿼리 51번이다(§22 렌즈 7 N+1).
    """
    if not user_ids:
        return {}
    rows = session.execute(
        select(UserRole.user_id, Role.code)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserRole.user_id.in_(user_ids),
            UserRole.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
    ).all()
    grouped: dict[int, set[RoleCode]] = {user_id: set() for user_id in user_ids}
    for user_id, code in rows:
        grouped[user_id].add(RoleCode(code))
    return grouped


def list_users(*, offset: int, limit: int) -> tuple[list[UserView], int]:
    """살아 있는 사용자 목록과 전체 건수. 페이지네이션은 호출부가 강제한다(§18.4)."""
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        ).scalar_one()
        users = list(
            session.execute(
                select(User)
                .where(User.deleted_at.is_(None))
                .order_by(User.id)
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        roles = _roles_by_user(session, [user.id for user in users])
        return [
            UserView(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                is_active=user.is_active,
                roles=frozenset(roles.get(user.id, set())),
            )
            for user in users
        ], total


def get_user(user_id: int) -> UserView | None:
    with unit_of_work() as uow:
        session = uow.session
        user = session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        ).scalar_one_or_none()
        if user is None:
            return None
        return UserView(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=_load_roles(session, user.id),
        )


def grant_role(*, user_id: int, role: RoleCode, actor_user_id: int) -> UserView:
    """역할을 부여한다. 이미 있으면 아무것도 하지 않는다(재요청이 에러가 아니다)."""
    with unit_of_work() as uow:
        session = uow.session
        user = session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError(log_context={"user_id": user_id})

        role_id = session.execute(
            select(Role.id).where(Role.code == role.value, Role.deleted_at.is_(None))
        ).scalar_one()
        existing = session.execute(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role_id,
                UserRole.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(UserRole(user_id=user_id, role_id=role_id, created_by_id=actor_user_id))
            audit.record(
                session,
                action=AuditAction.ROLE_GRANTED,
                actor_user_id=actor_user_id,
                entity_type="users",
                entity_id=user_id,
                detail={"role": role.value},
            )
        session.flush()
        return UserView(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=_load_roles(session, user.id),
        )


def revoke_role(*, user_id: int, role: RoleCode, actor_user_id: int) -> UserView:
    """역할을 회수한다(soft delete). 없으면 아무것도 하지 않는다."""
    with unit_of_work() as uow:
        session = uow.session
        user = session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError(log_context={"user_id": user_id})

        assignment = session.execute(
            select(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user_id,
                Role.code == role.value,
                UserRole.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if assignment is not None:
            assignment.deleted_at = utcnow()
            assignment.updated_by_id = actor_user_id
            audit.record(
                session,
                action=AuditAction.ROLE_REVOKED,
                actor_user_id=actor_user_id,
                entity_type="users",
                entity_id=user_id,
                detail={"role": role.value},
            )
        session.flush()
        return UserView(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=_load_roles(session, user.id),
        )
