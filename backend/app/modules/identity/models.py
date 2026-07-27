"""사용자·역할·세션 모델 (DESIGN.md §2 권한·통제 / §18.1 / ADR-0013).

VersionMixin(낙관적 잠금)을 붙이는 기준 — **사람이 화면에서 고치는 테이블만**.
§17.2가 version을 요구하는 대상은 "전표 헤더·주요 마스터"다. roles는 마이그레이션
으로만 바뀌고, user_roles는 부여/회수가 행 추가·soft delete라 편집 자체가 없으며,
user_sessions는 시스템이 관리한다. 안 쓰는 곳에 version을 달면 매 UPDATE마다
의미 없는 충돌 처리 코드가 따라붙는다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import unique_active, value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)


class RoleCode(StrEnum):
    """역할 5종 (§2 권한·통제). **인가 판단의 권위는 이 열거형이다.**

    ADR-11은 "유형 코드는 데이터, 검증 골격은 코드 고정"이라고 나눈다. 역할은
    인가 판단에 직접 쓰이므로 코드 고정이고, roles 테이블은 표시명과 FK 무결성을
    맡는다. 두 곳이 어긋나면 조용한 권한 사고가 나므로 roles.code에 같은 5종을
    CHECK로 걸어 DB가 강제한다.
    """

    ADMIN = "ADMIN"
    TRADE = "TRADE"
    LOGISTICS = "LOGISTICS"
    CERT = "CERT"
    VIEWER = "VIEWER"


#: 시드 값 — 마이그레이션이 이 목록 그대로 INSERT한다.
ROLE_SEED: tuple[tuple[RoleCode, str, str], ...] = (
    (RoleCode.ADMIN, "관리자", "전 기능·사용자·설정 관리"),
    (RoleCode.TRADE, "무역", "견적·수주·선적·채권"),
    (RoleCode.LOGISTICS, "물류", "재고·입출고·검수·실사"),
    (RoleCode.CERT, "인증", "인증 요건·원산지 판정·문서"),
    (RoleCode.VIEWER, "조회", "읽기 전용 — 원가·마진은 응답 레벨에서 마스킹(§18.1)"),
)


class Role(PkMixin, TimestampMixin, SoftDeleteMixin, Base):
    """역할 마스터. 5종 고정 — 추가·변경은 마이그레이션으로만."""

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name_ko: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"))

    __table_args__ = (
        value_in("code", [role.value for role in RoleCode]),
        unique_active("roles", "code"),
    )


class User(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """사용자 계정.

    is_active는 §2의 "계정 비활성 절차"다 — 퇴사·휴가 시 계정을 지우지 않고
    끄는 자리. soft delete와 다른 상태다(비활성은 되돌리는 조치, 삭제는 아니다).
    """

    __tablename__ = "users"

    # 로그인 ID. 대소문자만 다른 중복 계정이 생기면 감사 추적이 갈라지므로
    # 소문자 정규화를 DB가 강제한다(앱이 깜빡해도 INSERT가 거부된다).
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(60), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # 연속 실패 5회 잠금 (§18.1). 성공 시 0으로 리셋.
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("failed_login_count >= 0", name="failed_login_count_nonnegative"),
        unique_active("users", "email"),
    )


class UserRole(PkMixin, TimestampMixin, SoftDeleteMixin, ActorMixin, Base):
    """사용자×역할 (겸직 허용 — 무역+인증처럼 실제로 흔하다).

    회수는 soft delete다. 재부여는 §17.4대로 부활이 아니라 신규 행이며,
    부분 유니크 인덱스가 그 재유입을 막지 않는다.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (unique_active("user_roles", "user_id", "role_id"),)


class UserSession(PkMixin, TimestampMixin, Base):
    """로그인 세션 (ADR-0013).

    ★ 쿠키에 실리는 원문 토큰은 저장하지 않는다 — sha256 해시만 남긴다.
      DB가 유출돼도 그 값으로 남의 세션을 탈취할 수 없어야 한다(§18.1).

    soft delete를 쓰지 않는 이유: 세션의 수명은 만료(expires_at)와
    폐기(revoked_at)라는 업무 개념이 이미 표현한다. 거기에 deleted_at을 더하면
    "살아 있는 세션"의 정의가 조건 3개로 늘어나고, 그 조건 하나를 빠뜨린
    쿼리가 곧 인증 우회가 된다.
    """

    __tablename__ = "user_sessions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        # 로그인 시 기존 세션 폐기(세션 고정 공격 차단)와 만료 세션 청소가
        # 모두 user_id·expires_at으로 훑는다.
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )
