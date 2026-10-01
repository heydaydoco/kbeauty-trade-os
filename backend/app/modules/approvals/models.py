"""승인 4테이블 — 결재선·승인 본체·승인 이력·대결 (S3-1 ADR-0060·0061 / design-C C1·C3·C5·C6).

■ `approval_lines`  결재선 매핑(유형×통화×임계 → 결재 역할). 설정 데이터 — **시드하지 않는다**(임계는 업무 정책이라
  코드가 지어낼 수 없다 — 함정 ⑩). 행 = "이 (유형·통화)에서 금액이 임계를 **초과(strict >)** 하면 이 역할이 결재한다".
■ `approvals`       요청·결정 본체. **SoftDelete 없음**(삭제 개념 없는 기록 — ADR-02 예외 명문화). 스냅샷 컬럼
  (유형·대상·금액·digest·required_role·요청자…)은 **컬럼 단위 UPDATE 권한으로 INSERT 이후 DB가 불변**으로 강제한다
  (`COLUMN_UPDATE_ALLOWLIST` — 트리거는 ADR-0028·0040이 기각한 계보). 상태·결정·소비 컬럼만 `_record_transition` 1통로가 바꾼다.
■ `approval_events` 상태 변경 이력 — IMMUTABLE(REVOKE). **시스템 행위자 없음**(actor NOT NULL — 자동 결정 경로 부재의 DB 표현).
■ `delegations`     대결(위임) — 당사자·기간·범위는 INSERT 이후 불변(컬럼 권한), 종료(revoke)만 UPDATE. SoftDelete 없음.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.constraints import value_in
from app.core.db.mixins import (
    ActorMixin,
    PkMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)
from app.modules.approvals.machine import (
    ACTIVE_STATUSES,
    ALLOWED_PAIRS_SQL,
    APPROVAL_TYPES,
    APPROVER_ROLES,
    REASON_MAX,
    TARGET_TYPES,
    TYPE_TARGET,
    ApprovalStatus,
    VoidReasonCode,
)

#: 금액 컬럼 상한(JS 안전 정수 — 전 금액 컬럼 공통 규약).
MAX_MINOR_AMOUNT = 2**53 - 1

_ACTIVE_PREDICATE = "status IN (" + ", ".join(f"'{s.value}'" for s in ACTIVE_STATUSES) + ")"
_TYPE_TARGET_SQL = " OR ".join(
    f"(approval_type = '{t}' AND target_type = '{target}')" for t, target in TYPE_TARGET.items()
)
_ALL_STATUSES = [s.value for s in ApprovalStatus]


class ApprovalLine(PkMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, ActorMixin, Base):
    """결재선 매핑 1행 — (유형, 통화)에서 `threshold_amount`를 **초과**하는 금액을 `approver_role`이 결재한다."""

    __tablename__ = "approval_lines"

    approval_type: Mapped[str] = mapped_column(String(30), nullable=False)
    threshold_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    threshold_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    approver_role: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    __table_args__ = (
        value_in("approval_type", APPROVAL_TYPES, name="approval_type_valid"),
        CheckConstraint(
            f"threshold_amount >= 0 AND threshold_amount <= {MAX_MINOR_AMOUNT}",
            name="threshold_amount_range",
        ),
        CheckConstraint("threshold_currency ~ '^[A-Z]{3}$'", name="threshold_currency_upper"),
        value_in("approver_role", APPROVER_ROLES, name="approver_role_valid"),
        CheckConstraint("note IS NULL OR btrim(note) <> ''", name="note_not_blank"),
        # 같은 (유형·통화)에서 임계 중복을 DB가 막는다 → 선택 규칙이 결정적이다.
        # `unique_active` 헬퍼는 이름이 63자를 넘어 못 쓴다 — 수기 인덱스. 임계 금액이 유니크 키에 든 예외는
        # test_secret_boundaries.ALLOWED_SENSITIVE_UNIQUE_KEYS에 사유와 함께 등록돼 있다(ADR-0018 ㉠ 부기).
        Index(
            "uq_approval_lines_threshold_active",
            "approval_type",
            "threshold_currency",
            "threshold_amount",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class Delegation(PkMixin, TimestampMixin, VersionMixin, ActorMixin, Base):
    """대결(위임) — (승인 유형 1개, 위임하는 결재 역할 1개) 범위의 기간 한정 권한 위탁. 유효성은 **계산값**이다(스윕 없음)."""

    __tablename__ = "delegations"

    delegator_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    delegate_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approval_type: Mapped[str] = mapped_column(String(30), nullable=False)
    delegated_role: Mapped[str] = mapped_column(String(20), nullable=False)
    #: KST 달력 날짜, 양끝 포함.
    start_on: Mapped[date] = mapped_column(Date, nullable=False)
    end_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        value_in("approval_type", APPROVAL_TYPES, name="approval_type_valid"),
        value_in("delegated_role", APPROVER_ROLES, name="delegated_role_valid"),
        CheckConstraint("delegator_user_id <> delegate_user_id", name="parties_differ"),
        CheckConstraint("end_on >= start_on", name="period_order"),
        CheckConstraint("(revoked_at IS NULL) = (revoked_by_id IS NULL)", name="revoked_pair"),
        CheckConstraint("note IS NULL OR btrim(note) <> ''", name="note_not_blank"),
        # 더블클릭·재전송 DB 방어망 — 같은 위임자·유형·역할·시작일의 미종료 대결은 하나뿐.
        Index(
            "uq_delegations_start_active",
            "delegator_user_id",
            "approval_type",
            "delegated_role",
            "start_on",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_delegations_delegate",
            "delegate_user_id",
            "start_on",
            "end_on",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_delegations_delegator", "delegator_user_id"),
    )


class Approval(PkMixin, TimestampMixin, VersionMixin, ActorMixin, Base):
    """승인 요청·결정 본체 — 스냅샷 컬럼은 INSERT 이후 DB 권한으로 불변이다."""

    __tablename__ = "approvals"

    approval_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: 폴리모픽 — FK 없음(documents·tasks 선례, ADR-0028 계보). 실재는 TargetSpec이 서비스에서 검증한다.
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 요청 시점 표시명(전표번호) 동결 — 결재함 목록의 폴리모픽 N+1 방지.
    target_label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text(f"'{ApprovalStatus.REQUESTED.value}'")
    )
    requested_by_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: 승인이 허용하는 금액 상한 — SO_CREDIT_EXCEEDED에서는 '여신 초과분'(한도 통화).
    basis_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    basis_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    #: 승인 결속 토큰(sha256 hex) — version이 아니라 **대상 판정 입력 내용의 digest**다(X-07: 라인만 바뀌고 부모
    #: version이 안 오르는 S1-3 결함 유형·메모 변경으로 인한 불필요한 재승인 압력 모두 회피).
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 표시용 — **판정에 쓰지 않는다**(키 추가는 호환).
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: 요청 시점 동결(결재선 변경이 진행 중 승인에 소급하지 않는다 — C3).
    required_role: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_line_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_lines.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decided_on_behalf_of_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decided_delegation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("delegations.id", ondelete="RESTRICT"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    __table_args__ = (
        value_in("approval_type", APPROVAL_TYPES, name="approval_type_valid"),
        value_in("target_type", TARGET_TYPES, name="target_type_valid"),
        value_in("status", _ALL_STATUSES, name="status_valid"),
        value_in("required_role", APPROVER_ROLES, name="required_role_valid"),
        CheckConstraint(_TYPE_TARGET_SQL, name="type_target_pair"),
        CheckConstraint(
            f"basis_amount > 0 AND basis_amount <= {MAX_MINOR_AMOUNT}", name="basis_amount_positive"
        ),
        CheckConstraint("basis_currency ~ '^[A-Z]{3}$'", name="basis_currency_upper"),
        CheckConstraint("snapshot_digest ~ '^[0-9a-f]{64}$'", name="snapshot_digest_format"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot_is_object"),
        CheckConstraint("btrim(target_label) <> ''", name="target_label_not_blank"),
        CheckConstraint("(decided_by_id IS NULL) = (decided_at IS NULL)", name="decided_pair"),
        # 요청 중이면 결정 시각이 없고, 승인·반려·소비는 결정 시각이 있다(회수·무효는 어느 쪽에서든 올 수 있다).
        CheckConstraint(
            "(status = 'REQUESTED' AND decided_at IS NULL)"
            " OR (status IN ('APPROVED', 'REJECTED', 'CONSUMED') AND decided_at IS NOT NULL)"
            " OR status IN ('WITHDRAWN', 'VOIDED')",
            name="decided_state",
        ),
        # 직무분리(SoD)의 마지막 방어선 — 기안자는 결정자가 될 수 없다(ADMIN 포함, 예외 없음).
        CheckConstraint("decided_by_id IS NULL OR decided_by_id <> requested_by_id", name="sod"),
        CheckConstraint(
            "(decided_on_behalf_of_id IS NULL) = (decided_delegation_id IS NULL)",
            name="on_behalf_pair",
        ),
        CheckConstraint(
            "decided_on_behalf_of_id IS NULL OR decided_by_id IS NOT NULL",
            name="on_behalf_needs_decider",
        ),
        CheckConstraint(
            "decided_on_behalf_of_id IS NULL OR decided_on_behalf_of_id <> requested_by_id",
            name="on_behalf_not_requester",
        ),
        CheckConstraint("(status = 'CONSUMED') = (consumed_at IS NOT NULL)", name="consumed_pair"),
        CheckConstraint(
            "(consumed_at IS NULL) = (consumed_by_id IS NULL)", name="consumed_by_pair"
        ),
        # 대상당 활성 승인은 1건 — 종결 4태 밖이라 재기안은 신규 행이다.
        Index(
            "uq_approvals_active_target",
            "approval_type",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text(_ACTIVE_PREDICATE),
        ),
        Index(
            "ix_approvals_inbox",
            "required_role",
            "id",
            postgresql_where=text(f"status = '{ApprovalStatus.REQUESTED.value}'"),
        ),
        Index("ix_approvals_requested_by", "requested_by_id", "id"),
        Index("ix_approvals_target", "target_type", "target_id", "id"),
    )


class ApprovalEvent(PkMixin, Base):
    """승인 상태 변경 이력 — IMMUTABLE(REVOKE). 시스템 행위자가 없다(`actor_user_id` NOT NULL)."""

    __tablename__ = "approval_events"

    approval_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approvals.id", ondelete="RESTRICT"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: 생성 이벤트만 NULL.
    from_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    to_status: Mapped[str] = mapped_column(String(12), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    on_behalf_of_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    delegation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("delegations.id", ondelete="RESTRICT"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: VOIDED 전용 구조화 코드.
    reason_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        value_in("from_status", _ALL_STATUSES, name="from_status_valid"),
        value_in("to_status", _ALL_STATUSES, name="to_status_valid"),
        # 상태 기계를 DB가 한 번 더 강제한다 — machine.ALLOWED에서 생성(이중 정의 방지).
        CheckConstraint(
            # 탄생(from NULL)은 REQUESTED로만. `from_status IS NOT NULL` 가드가 필수다 — 없으면 from이 NULL일 때 두 번째 항이 NULL이 되어
            # CHECK가 통과해 버린다(SQL의 NULL = 위반 아님): 이력에 NULL→APPROVED 같은 탄생 행이 들어가는 구멍이 생긴다.
            f"(from_status IS NULL AND to_status = 'REQUESTED')"
            f" OR (from_status IS NOT NULL AND ({ALLOWED_PAIRS_SQL}))",
            name="pair_allowed",
        ),
        CheckConstraint(
            "(on_behalf_of_user_id IS NULL) = (delegation_id IS NULL)", name="delegation_pair"
        ),
        CheckConstraint(
            "on_behalf_of_user_id IS NULL OR on_behalf_of_user_id <> actor_user_id",
            name="actor_not_on_behalf",
        ),
        CheckConstraint(
            "to_status NOT IN ('REJECTED', 'WITHDRAWN') OR nullif(btrim(reason), '') IS NOT NULL",
            name="reason_required",
        ),
        CheckConstraint(
            f"reason IS NULL OR char_length(reason) <= {REASON_MAX}", name="reason_len"
        ),
        CheckConstraint("(to_status = 'VOIDED') = (reason_code IS NOT NULL)", name="void_code"),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN ("
            + ", ".join(f"'{c.value}'" for c in sorted(VoidReasonCode, key=lambda c: c.value))
            + ")",
            name="reason_code_valid",
        ),
        Index("ix_approval_events_approval", "approval_id", "id"),
    )
