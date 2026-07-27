"""audit_log — 첫 불변 테이블 (DESIGN.md §17.5 / §18.1).

★ 공통 믹스인을 대부분 쓰지 않는다. 이유가 각각 다르다.
    TimestampMixin  updated_at이 있다는 것 자체가 "고칠 수 있다"는 뜻이다.
                    불변 테이블에서는 모순이므로 `at` 하나만 둔다.
    SoftDeleteMixin 지울 수 없는 로그에 deleted_at은 지울 수 있다는 신호다.
    VersionMixin    UPDATE가 없으니 낙관적 잠금의 대상이 없다.
    ActorMixin      행위자는 actor_user_id 하나로 명시한다. created_by/updated_by
                    두 칸은 "누가 고쳤나"를 물을 수 있게 만들어 오해를 부른다.

권한 회수는 마이그레이션에서 revoke_mutations(op, "audit_log")가 한다 —
app/core/db/table_policy.py의 IMMUTABLE_TABLES 등록이 선행 조건이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import PkMixin


class AuditAction:
    """감사 액션 코드.

    CHECK로 고정하지 않는다 — 액션은 세션마다 늘어나고, 그때마다 마이그레이션을
    강요하면 "일단 기존 코드 재사용"이라는 최악의 회피가 생긴다. 대신 상수로
    모아 두어 오타가 리뷰에서 보이게 한다.
    """

    LOGIN_SUCCEEDED = "auth.login.succeeded"
    LOGIN_FAILED = "auth.login.failed"
    LOGIN_BLOCKED = "auth.login.blocked"
    LOGOUT = "auth.logout"
    SESSION_REVOKED = "auth.session.revoked"
    ACCESS_DENIED = "auth.access.denied"
    ROLE_GRANTED = "identity.role.granted"
    ROLE_REVOKED = "identity.role.revoked"
    ACCOUNT_ACTIVATED = "identity.account.activated"
    ACCOUNT_DEACTIVATED = "identity.account.deactivated"
    ACCOUNT_UNLOCKED = "identity.account.unlocked"
    #: 담당 일괄 이관 (§2 "담당 이관" / ADR-0015).
    ASSIGNMENTS_HANDED_OVER = "identity.assignments.handed_over"
    #: 마지막 관리자를 지우려는 시도. 막힌 시도야말로 남아야 하는 기록이다.
    LAST_ADMIN_PROTECTED = "identity.admin.last_one_protected"


class AuditLog(PkMixin, Base):
    """불변 감사 로그. INSERT/SELECT만 가능하다(앱 계정 기준)."""

    __tablename__ = "audit_log"

    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # NULL 허용 — 존재하지 않는 이메일로 로그인을 시도하면 행위자를 특정할 수
    # 없다. 그 시도야말로 반드시 남아야 하는 기록이다.
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # §18.3 — 사용자 신고(화면의 오류 번호)와 로그를 잇는 열쇠.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 부가 맥락. 민감값을 넣지 않는다(§18.1) — 여기 들어간 값은 마스킹 프로세서를
    # 거치지 않고 DB에 그대로 남는다.
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("ix_audit_log_at", "at"),
        Index("ix_audit_log_actor_user_id", "actor_user_id"),
        Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),
    )
