"""agency collaboration schema

리비전 ID: 1df4a398d38a
직전 리비전: f619b469c74d
생성 시각(UTC): 2026-09-29 13:35:03.037632+00:00

대행 협업 스키마 (§5.4 / WBS S2-4 / s2-4-plan.md §2 안건 ① — ADR-0048):
  ① 신규 테이블 2 — agency_contracts(대행 계약 대장)·comm_logs(폴리모픽 통신 기록)
  ② certifications 컬럼 4 — handling_mode·action_owner·action_owner_changed_on·
     agency_partner_id (+ CHECK 4·FK·인덱스)
  ③ documents 소유 열거 CHECK 재정의 — 'COMM_LOG' 추가(통신 기록 첨부)

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ rename 없음 — 신규 테이블 2개 + 기존 테이블 컬럼 추가뿐.
  ■ **기존 테이블(certifications·documents)에 붙는 CHECK는 autogenerate가
    감지하지 못한다(함정 ①)** — 초안에는 신규 테이블 안의 CHECK만 있었고,
    아래 CHECK 4건(certifications)과 documents 소유 열거 재정의는 전부 수기다.
    op.f()를 붙였다(함정 ⑪ — 평문 이름은 naming convention이 한 번 더 접두한다).
      · handling_mode_valid / action_owner_valid — 열거 2종(models와 1:1)
      · ★ handling_agency_pair — (처리방식=AGENCY) = (대행사 지정) 양방향:
        "대행인데 누구인지 모름"·"직접인데 대행사가 붙어 있음" 차단
      · agency_owner_requires_agency — 공이 대행사에 있으면 대행 처리여야 함
  ■ 컬럼 추가는 server_default('DIRECT'·'INTERNAL')로 기존 행을 채운다 — 기존
    행은 전부 직접 처리·사내 공으로 읽힌다(사실 그대로: 지금까지 대행 기록
    수단이 없었다). action_owner_changed_on은 NULL 허용(생성일로 읽는 규칙 —
    백필 없음).
  ■ 신규 테이블 CHECK 전건은 create_table 안에 자동 포함 — 모델과 1:1 대조 완료.
    금액 규약 3종(fee_pair·fee_amount_nonnegative·fee_currency_uppercase —
    partners.credit_limit_* 선례)·기간 순서·공백 금지·통신 기록의 기한 규칙 3종
    (follow_up_requires_action·due_after_occurred·done_after_occurred).
  ■ documents 소유 CHECK 정의 — 구: owner_type IN ('CERTIFICATION', 'LABEL',
    'SKU') / 신(models.value_in이 정렬한 것과 같은 열거): owner_type IN
    ('CERTIFICATION', 'COMM_LOG', 'LABEL', 'SKU'). 'COMM_LOG'(8자)는 기존
    VARCHAR(13) 폭 안이라 확폭 없음. 정의문 실재는 pg_get_constraintdef 테스트가
    지킨다(tests/integration/test_document_constraints.py).
  ■ 시드 없음 — PRESERVED_TABLES 비대상(함정 ⑩ 비적용).
  ■ table_policy: agency_contracts·comm_logs 모두 MUTABLE 등록(편집 마스터 —
    다음 액션 완료 처리·정정이 UPDATE).
  ■ downgrade는 역순 완결 — documents CHECK 구 정의 복원 → certifications
    FK·인덱스·컬럼(CHECK는 컬럼과 함께 소멸하나 명시 drop) → 테이블 2 drop.
    'COMM_LOG' 소유 문서 행이 이미 있으면 CHECK 재생성이 실패한다(데이터가
    제약보다 넓다) — 올바른 실패다, 행을 지우는 것은 downgrade의 일이 아니다.
    대행 협업 데이터(계약·통신 기록·대행 지정)는 downgrade로 소실된다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1df4a398d38a"
down_revision: str | None = "f619b469c74d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOC_CHECK = "ck_documents_owner_type_valid"
_DOC_OLD = "owner_type IN ('CERTIFICATION', 'LABEL', 'SKU')"
_DOC_NEW = "owner_type IN ('CERTIFICATION', 'COMM_LOG', 'LABEL', 'SKU')"


def upgrade() -> None:
    op.create_table(
        "agency_contracts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_no", sa.String(length=60), nullable=False),
        sa.Column("scope_note", sa.Text(), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=True),
        sa.Column("fee_amount", sa.BigInteger(), nullable=True),
        sa.Column("fee_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
            "(fee_amount IS NULL AND fee_currency IS NULL)"
            " OR (fee_amount IS NOT NULL AND fee_currency IS NOT NULL)",
            name=op.f("ck_agency_contracts_fee_pair"),
        ),
        sa.CheckConstraint(
            "end_on IS NULL OR end_on >= start_on",
            name=op.f("ck_agency_contracts_period_order"),
        ),
        sa.CheckConstraint(
            "fee_amount >= 0", name=op.f("ck_agency_contracts_fee_amount_nonnegative")
        ),
        sa.CheckConstraint(
            "fee_currency = upper(fee_currency)",
            name=op.f("ck_agency_contracts_fee_currency_uppercase"),
        ),
        sa.CheckConstraint(
            "length(btrim(contract_no)) > 0",
            name=op.f("ck_agency_contracts_contract_no_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_agency_contracts_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.id"],
            name=op.f("fk_agency_contracts_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_agency_contracts_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agency_contracts")),
    )
    op.create_index(
        "uq_agency_contracts_partner_id_contract_no_active",
        "agency_contracts",
        ["partner_id", "contract_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "comm_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=True),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("next_action", sa.String(length=500), nullable=True),
        sa.Column("next_action_due", sa.Date(), nullable=True),
        sa.Column("next_action_done_on", sa.Date(), nullable=True),
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
            "subject_type IN ('CERTIFICATION')", name=op.f("ck_comm_logs_subject_type_valid")
        ),
        sa.CheckConstraint(
            "length(btrim(summary)) > 0", name=op.f("ck_comm_logs_summary_not_blank")
        ),
        sa.CheckConstraint(
            "next_action IS NOT NULL OR (next_action_due IS NULL AND next_action_done_on IS NULL)",
            name=op.f("ck_comm_logs_follow_up_requires_action"),
        ),
        sa.CheckConstraint(
            "next_action IS NULL OR length(btrim(next_action)) > 0",
            name=op.f("ck_comm_logs_next_action_not_blank"),
        ),
        sa.CheckConstraint(
            "next_action_done_on IS NULL OR next_action_done_on >= occurred_on",
            name=op.f("ck_comm_logs_done_after_occurred"),
        ),
        sa.CheckConstraint(
            "next_action_due IS NULL OR next_action_due >= occurred_on",
            name=op.f("ck_comm_logs_due_after_occurred"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_comm_logs_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.id"],
            name=op.f("fk_comm_logs_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_comm_logs_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comm_logs")),
    )
    op.create_index(
        "ix_comm_logs_subject_type_subject_id",
        "comm_logs",
        ["subject_type", "subject_id"],
        unique=False,
    )

    # ── certifications — 대행 협업 컬럼 4 ──
    op.add_column(
        "certifications",
        sa.Column("handling_mode", sa.String(length=6), server_default="DIRECT", nullable=False),
    )
    op.add_column(
        "certifications",
        sa.Column("action_owner", sa.String(length=9), server_default="INTERNAL", nullable=False),
    )
    op.add_column("certifications", sa.Column("action_owner_changed_on", sa.Date(), nullable=True))
    op.add_column("certifications", sa.Column("agency_partner_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_certifications_agency_partner_id_partners"),
        "certifications",
        "partners",
        ["agency_partner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_certifications_agency_partner_id",
        "certifications",
        ["agency_partner_id"],
        unique=False,
    )
    # 수기 — 기존 테이블에 붙는 CHECK는 autogenerate가 감지하지 못한다(함정 ①).
    op.create_check_constraint(
        op.f("ck_certifications_handling_mode_valid"),
        "certifications",
        "handling_mode IN ('AGENCY', 'DIRECT')",
    )
    op.create_check_constraint(
        op.f("ck_certifications_action_owner_valid"),
        "certifications",
        "action_owner IN ('AGENCY', 'AUTHORITY', 'INTERNAL')",
    )
    op.create_check_constraint(
        op.f("ck_certifications_handling_agency_pair"),
        "certifications",
        "(handling_mode = 'AGENCY') = (agency_partner_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_certifications_agency_owner_requires_agency"),
        "certifications",
        "action_owner <> 'AGENCY' OR handling_mode = 'AGENCY'",
    )

    # ── documents — 소유 열거 재정의 (COMM_LOG 추가) ──
    op.drop_constraint(op.f(_DOC_CHECK), "documents", type_="check")
    op.create_check_constraint(op.f(_DOC_CHECK), "documents", _DOC_NEW)


def downgrade() -> None:
    op.drop_constraint(op.f(_DOC_CHECK), "documents", type_="check")
    op.create_check_constraint(op.f(_DOC_CHECK), "documents", _DOC_OLD)

    op.drop_constraint(
        op.f("ck_certifications_agency_owner_requires_agency"), "certifications", type_="check"
    )
    op.drop_constraint(
        op.f("ck_certifications_handling_agency_pair"), "certifications", type_="check"
    )
    op.drop_constraint(
        op.f("ck_certifications_action_owner_valid"), "certifications", type_="check"
    )
    op.drop_constraint(
        op.f("ck_certifications_handling_mode_valid"), "certifications", type_="check"
    )
    op.drop_index("ix_certifications_agency_partner_id", table_name="certifications")
    op.drop_constraint(
        op.f("fk_certifications_agency_partner_id_partners"), "certifications", type_="foreignkey"
    )
    op.drop_column("certifications", "agency_partner_id")
    op.drop_column("certifications", "action_owner_changed_on")
    op.drop_column("certifications", "action_owner")
    op.drop_column("certifications", "handling_mode")

    op.drop_index("ix_comm_logs_subject_type_subject_id", table_name="comm_logs")
    op.drop_table("comm_logs")
    op.drop_index(
        "uq_agency_contracts_partner_id_contract_no_active",
        table_name="agency_contracts",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("agency_contracts")
