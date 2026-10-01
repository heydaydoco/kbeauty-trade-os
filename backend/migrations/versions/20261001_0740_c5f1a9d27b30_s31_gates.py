"""s31_gates

리비전 ID: c5f1a9d27b30
직전 리비전: a0d0ad33604e
생성 시각(UTC): 2026-10-01 07:40:00+00:00

S3-1 PR-11a 게이트 증적 — M09 (ADR-0069 / design-D D3 / design-integrated §2.1 (f)):
  `gate_evaluations` — 확정 시도 1건의 게이트 평가 스냅샷(INSERT-only). `UNIQUE(subject_type, subject_id) WHERE outcome='CONFIRMED'`가 SO당 확정 증거를 1행으로 묶는다.
  `gate_overrides`   — 통제된 예외 부여(GRANT)·철회(REVOKE) 기록(INSERT-only). `gate_code` CHECK가 override 가능한 4종만 열거해 ITEM_MAPPING·DUPLICATE_PO·CREDIT의
                       우회를 DB가 거부한다. 사유 5~500자(제어문자 불가)·판정 해시 64hex·행위자 역할 5종 CHECK.

체크리스트:
  ■ 신규 테이블 2개만(백필·기존 테이블 변경 없음). **시드 0** — 게이트 증적은 업무 행위의 기록이다(함정 ⑩, _NEVER_SEEDED 등재).
  ■ CHECK는 create_table 안 op.f() 최종 이름이다(alembic check가 CHECK를 못 보므로 정의문 테스트 tests/integration/test_gate_constraints.py가 고정).
  ■ **GRANT/REVOKE는 autogenerate가 못 보므로 손으로 부른다**: `revoke_mutations(op, ...)` 2회 — 앱 계정(kbos_app)은 INSERT·SELECT만(UPDATE·DELETE·TRUNCATE 42501).
  ■ `subject_id`·`line_id`는 폴리모픽(S5-1이 `subject_type`에 CHANNEL_LISTING 추가)이라 FK를 두지 않는다. users FK 2개(evaluated_by_id·granted_by_id)는 ACTOR_LOG 분류.
  ■ 제약·인덱스 이름 63자 이내(최장 `ck_gate_overrides_authorized_role_valid` 40자 — 실측 테스트가 고정).
  ■ downgrade는 인덱스 → 테이블 역순 drop(권한은 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다(증적 — 운영 데이터가 생긴 뒤에는 되돌리지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db.table_policy import revoke_mutations

revision: str = "c5f1a9d27b30"
down_revision: str | None = "a0d0ad33604e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gate_evaluations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=9), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("evaluated_by_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject_type IN ('SALES_ORDER')", name=op.f("ck_gate_evaluations_subject_type_valid")
        ),
        sa.CheckConstraint(
            "outcome IN ('CONFIRMED', 'BLOCKED')", name=op.f("ck_gate_evaluations_outcome_valid")
        ),
        sa.CheckConstraint(
            "input_digest ~ '^[0-9a-f]{64}$'", name=op.f("ck_gate_evaluations_input_digest_format")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(results) = 'object'", name=op.f("ck_gate_evaluations_results_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["evaluated_by_id"],
            ["users.id"],
            name=op.f("fk_gate_evaluations_evaluated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gate_evaluations")),
    )
    op.create_index(
        "ix_gate_evaluations_evaluated_by_id", "gate_evaluations", ["evaluated_by_id"], unique=False
    )
    op.create_index(
        "ix_gate_evaluations_subject",
        "gate_evaluations",
        ["subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "uq_gate_evaluations_confirmed_once",
        "gate_evaluations",
        ["subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("outcome = 'CONFIRMED'"),
    )

    op.create_table(
        "gate_overrides",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("line_id", sa.BigInteger(), nullable=True),
        sa.Column("gate_code", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=6), nullable=False),
        sa.Column("result_at_grant", sa.String(length=7), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("basis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("basis_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("granted_by_id", sa.BigInteger(), nullable=False),
        sa.Column("authorized_role", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "subject_type IN ('SALES_ORDER')", name=op.f("ck_gate_overrides_subject_type_valid")
        ),
        sa.CheckConstraint(
            "gate_code IN ('MARKET_READINESS', 'MOQ', 'PI_DEPOSIT', 'PRICE_DEVIATION')",
            name=op.f("ck_gate_overrides_gate_code_overridable"),
        ),
        sa.CheckConstraint(
            "action IN ('GRANT', 'REVOKE')", name=op.f("ck_gate_overrides_action_valid")
        ),
        sa.CheckConstraint(
            "result_at_grant IN ('BLOCK', 'UNKNOWN')",
            name=op.f("ck_gate_overrides_result_at_grant_valid"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 5 AND 500",
            name=op.f("ck_gate_overrides_reason_length"),
        ),
        sa.CheckConstraint("reason !~ '[[:cntrl:]]'", name=op.f("ck_gate_overrides_reason_clean")),
        sa.CheckConstraint(
            "basis_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_gate_overrides_basis_hash_format")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(basis) = 'object'", name=op.f("ck_gate_overrides_basis_is_object")
        ),
        sa.CheckConstraint(
            "line_id IS NULL OR line_id > 0", name=op.f("ck_gate_overrides_line_id_positive")
        ),
        sa.CheckConstraint(
            "authorized_role IN ('ADMIN', 'CERT', 'LOGISTICS', 'TRADE', 'VIEWER')",
            name=op.f("ck_gate_overrides_authorized_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"],
            ["users.id"],
            name=op.f("fk_gate_overrides_granted_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gate_overrides")),
    )
    op.create_index(
        "ix_gate_overrides_granted_by_id", "gate_overrides", ["granted_by_id"], unique=False
    )
    op.create_index(
        "ix_gate_overrides_subject_gate",
        "gate_overrides",
        ["subject_type", "subject_id", "gate_code"],
        unique=False,
    )
    revoke_mutations(op, "gate_evaluations")
    revoke_mutations(op, "gate_overrides")


def downgrade() -> None:
    op.drop_index("ix_gate_overrides_subject_gate", table_name="gate_overrides")
    op.drop_index("ix_gate_overrides_granted_by_id", table_name="gate_overrides")
    op.drop_table("gate_overrides")
    op.drop_index(
        "uq_gate_evaluations_confirmed_once",
        table_name="gate_evaluations",
        postgresql_where=sa.text("outcome = 'CONFIRMED'"),
    )
    op.drop_index("ix_gate_evaluations_subject", table_name="gate_evaluations")
    op.drop_index("ix_gate_evaluations_evaluated_by_id", table_name="gate_evaluations")
    op.drop_table("gate_evaluations")
