"""s31_approvals_core

리비전 ID: e312f01426d4
직전 리비전: a4c0e7fd37db
생성 시각(UTC): 2026-10-01 01:07:13.853976+00:00

S3-1 PR-9a 승인 코어 스키마 — M07 (ADR-0060·0061 / design-integrated §2.1·§2.11 / design-C C1~C6):
  ① approval_lines — 결재선 매핑(유형×통화×임계 strict 초과 → 결재 역할). 임계 유니크(활성 한정)가 선택 규칙을 결정적으로 만든다.
     컬럼 UPDATE 허용: approver_role·note·deleted_at·version·updated_* (유형·통화·임계는 불변 — 변경=삭제+신규).
  ② delegations — 대결(위임): 당사자·기간·범위 불변(컬럼 권한), 종료(revoked_*)만 UPDATE. SoftDelete 없음.
  ③ approvals — 승인 본체: SoftDelete 없음, 활성(REQUESTED·APPROVED) 대상당 1건 부분 유니크, SoD CHECK(기안자≠결정자)·
     on_behalf_not_requester·decided_state·consumed_pair 등. **스냅샷 컬럼은 컬럼 단위 UPDATE 권한으로 INSERT 이후 DB가 불변**.
  ④ approval_events — 상태 변경 이력(IMMUTABLE — revoke_mutations). 시스템 행위자 없음(actor NOT NULL)·허용 전이 7쌍 CHECK.

체크리스트:
  ■ 신규 4테이블만(백필·기존 테이블 변경 없음) — 선행은 users(M00대)뿐이라 SO 모델과 무관하다(target_id는 FK 없는 폴리모픽).
  ■ CHECK는 create_table 안에 op.f() 최종 이름이다(alembic check가 CHECK를 못 보므로 정의문 테스트
    tests/integration/test_approval_constraints.py가 pg_get_constraintdef·pg_get_indexdef로 고정한다).
  ■ **시드 0** — 전부 ActorMixin(users FK) 소유 표라 마이그레이션 시드 금지(함정 ⑩). 결재선은 ADMIN 화면/API로만 공급한다.
  ■ GRANT/REVOKE는 autogenerate가 못 보므로 손으로 부른다: approval_events=revoke_mutations(IMMUTABLE),
    approvals·approval_lines·delegations=restrict_update_columns(COLUMN_UPDATE_ALLOWLIST와 짝). 순서 주의: 테이블 단위 UPDATE를
    먼저 회수한 뒤 컬럼 GRANT(테이블 권한이 남으면 컬럼 제한은 조용히 무효다 — 실측 테스트가 has_column_privilege로 고정).
  ■ downgrade는 인덱스 → 테이블 역순 drop(권한은 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db.table_policy import (
    COLUMN_UPDATE_ALLOWLIST,
    restrict_update_columns,
    revoke_mutations,
)

revision: str = "e312f01426d4"
down_revision: str | None = "a4c0e7fd37db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("approval_type", sa.String(length=30), nullable=False),
        sa.Column("threshold_amount", sa.BigInteger(), nullable=False),
        sa.Column("threshold_currency", sa.String(length=3), nullable=False),
        sa.Column("approver_role", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "approval_type IN ('SO_CREDIT_EXCEEDED')",
            name=op.f("ck_approval_lines_approval_type_valid"),
        ),
        sa.CheckConstraint(
            "approver_role IN ('ADMIN', 'CERT', 'LOGISTICS', 'TRADE')",
            name=op.f("ck_approval_lines_approver_role_valid"),
        ),
        sa.CheckConstraint(
            "note IS NULL OR btrim(note) <> ''", name=op.f("ck_approval_lines_note_not_blank")
        ),
        sa.CheckConstraint(
            "threshold_currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_approval_lines_threshold_currency_upper"),
        ),
        sa.CheckConstraint(
            "threshold_amount >= 0 AND threshold_amount <= 9007199254740991",
            name=op.f("ck_approval_lines_threshold_amount_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_approval_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_approval_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_lines")),
    )
    restrict_update_columns(op, "approval_lines", COLUMN_UPDATE_ALLOWLIST["approval_lines"])
    op.create_index(
        "uq_approval_lines_threshold_active",
        "approval_lines",
        ["approval_type", "threshold_currency", "threshold_amount"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "delegations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("delegator_user_id", sa.BigInteger(), nullable=False),
        sa.Column("delegate_user_id", sa.BigInteger(), nullable=False),
        sa.Column("approval_type", sa.String(length=30), nullable=False),
        sa.Column("delegated_role", sa.String(length=20), nullable=False),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "approval_type IN ('SO_CREDIT_EXCEEDED')",
            name=op.f("ck_delegations_approval_type_valid"),
        ),
        sa.CheckConstraint(
            "delegated_role IN ('ADMIN', 'CERT', 'LOGISTICS', 'TRADE')",
            name=op.f("ck_delegations_delegated_role_valid"),
        ),
        sa.CheckConstraint(
            "note IS NULL OR btrim(note) <> ''", name=op.f("ck_delegations_note_not_blank")
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_id IS NULL)",
            name=op.f("ck_delegations_revoked_pair"),
        ),
        sa.CheckConstraint(
            "delegator_user_id <> delegate_user_id", name=op.f("ck_delegations_parties_differ")
        ),
        sa.CheckConstraint("end_on >= start_on", name=op.f("ck_delegations_period_order")),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_delegations_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["delegate_user_id"],
            ["users.id"],
            name=op.f("fk_delegations_delegate_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["delegator_user_id"],
            ["users.id"],
            name=op.f("fk_delegations_delegator_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_id"],
            ["users.id"],
            name=op.f("fk_delegations_revoked_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_delegations_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delegations")),
    )
    restrict_update_columns(op, "delegations", COLUMN_UPDATE_ALLOWLIST["delegations"])
    op.create_index(
        "ix_delegations_delegate",
        "delegations",
        ["delegate_user_id", "start_on", "end_on"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_delegations_delegator", "delegations", ["delegator_user_id"], unique=False)
    op.create_index(
        "uq_delegations_start_active",
        "delegations",
        ["delegator_user_id", "approval_type", "delegated_role", "start_on"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("approval_type", sa.String(length=30), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("target_label", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'REQUESTED'"), nullable=False
        ),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=False),
        sa.Column("basis_amount", sa.BigInteger(), nullable=False),
        sa.Column("basis_currency", sa.String(length=3), nullable=False),
        sa.Column("snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("required_role", sa.String(length=20), nullable=False),
        sa.Column("approval_line_id", sa.BigInteger(), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_on_behalf_of_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_delegation_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(approval_type = 'SO_CREDIT_EXCEEDED' AND target_type = 'SALES_ORDER')",
            name=op.f("ck_approvals_type_target_pair"),
        ),
        sa.CheckConstraint(
            "(status = 'CONSUMED') = (consumed_at IS NOT NULL)",
            name=op.f("ck_approvals_consumed_pair"),
        ),
        sa.CheckConstraint(
            "(status = 'REQUESTED' AND decided_at IS NULL) OR (status IN ('APPROVED', 'REJECTED', 'CONSUMED') AND decided_at IS NOT NULL) OR status IN ('WITHDRAWN', 'VOIDED')",
            name=op.f("ck_approvals_decided_state"),
        ),
        sa.CheckConstraint(
            "approval_type IN ('SO_CREDIT_EXCEEDED')", name=op.f("ck_approvals_approval_type_valid")
        ),
        sa.CheckConstraint(
            "basis_currency ~ '^[A-Z]{3}$'", name=op.f("ck_approvals_basis_currency_upper")
        ),
        sa.CheckConstraint(
            "btrim(target_label) <> ''", name=op.f("ck_approvals_target_label_not_blank")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name=op.f("ck_approvals_snapshot_is_object")
        ),
        sa.CheckConstraint(
            "required_role IN ('ADMIN', 'CERT', 'LOGISTICS', 'TRADE')",
            name=op.f("ck_approvals_required_role_valid"),
        ),
        sa.CheckConstraint(
            "snapshot_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_approvals_snapshot_digest_format")
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'CONSUMED', 'REJECTED', 'REQUESTED', 'VOIDED', 'WITHDRAWN')",
            name=op.f("ck_approvals_status_valid"),
        ),
        sa.CheckConstraint(
            "target_type IN ('SALES_ORDER')", name=op.f("ck_approvals_target_type_valid")
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL) = (consumed_by_id IS NULL)",
            name=op.f("ck_approvals_consumed_by_pair"),
        ),
        sa.CheckConstraint(
            "(decided_by_id IS NULL) = (decided_at IS NULL)", name=op.f("ck_approvals_decided_pair")
        ),
        sa.CheckConstraint(
            "(decided_on_behalf_of_id IS NULL) = (decided_delegation_id IS NULL)",
            name=op.f("ck_approvals_on_behalf_pair"),
        ),
        sa.CheckConstraint(
            "basis_amount > 0 AND basis_amount <= 9007199254740991",
            name=op.f("ck_approvals_basis_amount_positive"),
        ),
        sa.CheckConstraint(
            "decided_by_id IS NULL OR decided_by_id <> requested_by_id",
            name=op.f("ck_approvals_sod"),
        ),
        sa.CheckConstraint(
            "decided_on_behalf_of_id IS NULL OR decided_by_id IS NOT NULL",
            name=op.f("ck_approvals_on_behalf_needs_decider"),
        ),
        sa.CheckConstraint(
            "decided_on_behalf_of_id IS NULL OR decided_on_behalf_of_id <> requested_by_id",
            name=op.f("ck_approvals_on_behalf_not_requester"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_line_id"],
            ["approval_lines.id"],
            name=op.f("fk_approvals_approval_line_id_approval_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["consumed_by_id"],
            ["users.id"],
            name=op.f("fk_approvals_consumed_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_approvals_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_approvals_decided_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_delegation_id"],
            ["delegations.id"],
            name=op.f("fk_approvals_decided_delegation_id_delegations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_on_behalf_of_id"],
            ["users.id"],
            name=op.f("fk_approvals_decided_on_behalf_of_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_approvals_requested_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_approvals_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    restrict_update_columns(op, "approvals", COLUMN_UPDATE_ALLOWLIST["approvals"])
    op.create_index(
        "ix_approvals_inbox",
        "approvals",
        ["required_role", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'REQUESTED'"),
    )
    op.create_index(
        "ix_approvals_requested_by", "approvals", ["requested_by_id", "id"], unique=False
    )
    op.create_index(
        "ix_approvals_target", "approvals", ["target_type", "target_id", "id"], unique=False
    )
    op.create_index(
        "uq_approvals_active_target",
        "approvals",
        ["approval_type", "target_type", "target_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('REQUESTED', 'APPROVED')"),
    )
    op.create_table(
        "approval_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=12), nullable=True),
        sa.Column("to_status", sa.String(length=12), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("on_behalf_of_user_id", sa.BigInteger(), nullable=True),
        sa.Column("delegation_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reason_code", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "(from_status IS NULL AND to_status = 'REQUESTED') OR ((from_status = 'APPROVED' AND to_status = 'CONSUMED') OR (from_status = 'APPROVED' AND to_status = 'VOIDED') OR (from_status = 'APPROVED' AND to_status = 'WITHDRAWN') OR (from_status = 'REQUESTED' AND to_status = 'APPROVED') OR (from_status = 'REQUESTED' AND to_status = 'REJECTED') OR (from_status = 'REQUESTED' AND to_status = 'VOIDED') OR (from_status = 'REQUESTED' AND to_status = 'WITHDRAWN'))",
            name=op.f("ck_approval_events_pair_allowed"),
        ),
        sa.CheckConstraint(
            "(to_status = 'VOIDED') = (reason_code IS NOT NULL)",
            name=op.f("ck_approval_events_void_code"),
        ),
        sa.CheckConstraint(
            "from_status IN ('APPROVED', 'CONSUMED', 'REJECTED', 'REQUESTED', 'VOIDED', 'WITHDRAWN')",
            name=op.f("ck_approval_events_from_status_valid"),
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN ('CAP_EXCEEDED', 'NOT_REQUIRED', 'TARGET_CANCELLED', 'TARGET_CHANGED')",
            name=op.f("ck_approval_events_reason_code_valid"),
        ),
        sa.CheckConstraint(
            "to_status IN ('APPROVED', 'CONSUMED', 'REJECTED', 'REQUESTED', 'VOIDED', 'WITHDRAWN')",
            name=op.f("ck_approval_events_to_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status NOT IN ('REJECTED', 'WITHDRAWN') OR nullif(btrim(reason), '') IS NOT NULL",
            name=op.f("ck_approval_events_reason_required"),
        ),
        sa.CheckConstraint(
            "(on_behalf_of_user_id IS NULL) = (delegation_id IS NULL)",
            name=op.f("ck_approval_events_delegation_pair"),
        ),
        sa.CheckConstraint(
            "on_behalf_of_user_id IS NULL OR on_behalf_of_user_id <> actor_user_id",
            name=op.f("ck_approval_events_actor_not_on_behalf"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR char_length(reason) <= 1000",
            name=op.f("ck_approval_events_reason_len"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_approval_events_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name=op.f("fk_approval_events_approval_id_approvals"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["delegation_id"],
            ["delegations.id"],
            name=op.f("fk_approval_events_delegation_id_delegations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["on_behalf_of_user_id"],
            ["users.id"],
            name=op.f("fk_approval_events_on_behalf_of_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_events")),
    )
    revoke_mutations(op, "approval_events")
    op.create_index(
        "ix_approval_events_approval", "approval_events", ["approval_id", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_approval_events_approval", table_name="approval_events")
    op.drop_table("approval_events")
    op.drop_index(
        "uq_approvals_active_target",
        table_name="approvals",
        postgresql_where=sa.text("status IN ('REQUESTED', 'APPROVED')"),
    )
    op.drop_index("ix_approvals_target", table_name="approvals")
    op.drop_index("ix_approvals_requested_by", table_name="approvals")
    op.drop_index(
        "ix_approvals_inbox",
        table_name="approvals",
        postgresql_where=sa.text("status = 'REQUESTED'"),
    )
    op.drop_table("approvals")
    op.drop_index(
        "uq_delegations_start_active",
        table_name="delegations",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index("ix_delegations_delegator", table_name="delegations")
    op.drop_index(
        "ix_delegations_delegate",
        table_name="delegations",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("delegations")
    op.drop_index(
        "uq_approval_lines_threshold_active",
        table_name="approval_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("approval_lines")
