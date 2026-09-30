"""s31_quotations

리비전 ID: a1905fcead96
직전 리비전: 0e42f85591de
생성 시각(UTC): 2026-09-30 05:43:38.827282+00:00

S3-1 PR-5a 견적(QT) 스키마 — M03 (ADR-0051~0054 / design-integrated §2.1·§2.11):
  ① quotations — 견적 헤더(동결 표식 frozen_at·복합 FK 대상 UNIQUE(id, currency)·전역 UNIQUE doc_number)
  ② quotation_lines — 견적 라인(자기 currency+헤더 복합 FK — 혼합 통화 불가능, Version 믹스인 없음)
  ③ quotation_status_log — 견적 상태 변경 이력(IMMUTABLE — revoke_mutations)

체크리스트:
  ■ 신규 테이블만(백필·기존 테이블 변경 없음). CHECK는 create_table 안에 있고 이름은 op.f()로 이미 최종
    이름이다(함정 ①·⑪ — alembic check가 CHECK를 못 보므로 정의문 테스트
    tests/integration/test_quotation_constraints.py가 pg_get_constraintdef로 고정한다).
  ■ 멱등·유일 키: doc_number는 **전역 UNIQUE**(부분 인덱스 아님 — 재발급 금지 §17.3, X-01), 라인
    (qt_id, line_no)·(qt_id, sku_id, is_free)는 unique_active 부분 인덱스(§17.4), 복제본 유일
    (copied_from_id)은 살아 있는 행 한정 부분 인덱스(X-08).
  ■ **시드 0** — quotations는 ActorMixin(users FK) 테이블이라 마이그레이션 시드 금지(함정 ⑩).
  ■ quotation_status_log는 REVOKE UPDATE, DELETE, TRUNCATE를 손으로 부른다(GRANT/REVOKE는 autogenerate가
    못 본다 — table_policy.IMMUTABLE_TABLES 등재와 짝).
  ■ downgrade는 인덱스 → 테이블 역순 drop(REVOKE는 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations


revision: str = "a1905fcead96"
down_revision: str | None = "0e42f85591de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quotations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("buyer_address", sa.String(length=500), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_amount", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("buyer_name", sa.String(length=200), nullable=False),
        sa.Column("buyer_partner_id", sa.BigInteger(), nullable=False),
        sa.Column("dest_market_code", sa.String(length=2), nullable=False),
        sa.Column("doc_number", sa.String(length=20), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("fx_rate_date", sa.Date(), nullable=True),
        sa.Column("payment_type", sa.String(length=12), nullable=True),
        sa.Column("advance_pct_bp", sa.Integer(), nullable=True),
        sa.Column("balance_anchor", sa.String(length=16), nullable=True),
        sa.Column("balance_days", sa.Integer(), nullable=True),
        sa.Column("incoterm_code", sa.String(length=3), nullable=True),
        sa.Column("incoterm_place", sa.String(length=100), nullable=True),
        sa.Column("incoterm_year", sa.SmallInteger(), nullable=True),
        sa.Column("internal_note", sa.String(length=1000), nullable=True),
        sa.Column("last_line_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=False),
        sa.Column("copied_from_id", sa.BigInteger(), nullable=True),
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
            "(payment_type IS NULL AND advance_pct_bp IS NULL AND balance_anchor IS NULL AND balance_days IS NULL) OR (payment_type = 'TT_ADVANCE' AND advance_pct_bp BETWEEN 1 AND 10000 AND ((advance_pct_bp = 10000 AND balance_anchor IS NULL AND balance_days IS NULL) OR (advance_pct_bp < 10000 AND balance_anchor IS NOT NULL AND balance_days IS NOT NULL))) OR (payment_type = 'TT_DEFERRED' AND advance_pct_bp IS NULL AND balance_anchor IS NOT NULL AND balance_days IS NOT NULL) OR (payment_type = 'LC' AND advance_pct_bp IS NULL AND balance_anchor IS NULL AND balance_days IS NULL)",
            name=op.f("ck_quotations_payment_terms_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' AND frozen_at IS NULL) OR (status IN ('ISSUED', 'CONVERTED') AND frozen_at IS NOT NULL) OR status IN ('CANCELLED', 'EXPIRED')",
            name=op.f("ck_quotations_frozen_matches_status"),
        ),
        sa.CheckConstraint(
            "balance_anchor IS NULL OR balance_anchor IN ('ARRIVAL_DATE', 'BL_DATE', 'ETD_DATE', 'INVOICE_DATE', 'ORDER_DATE', 'RECEIPT_DATE')",
            name=op.f("ck_quotations_balance_anchor_valid"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days >= 0 OR balance_anchor = 'ETD_DATE'",
            name=op.f("ck_quotations_neg_days_etd_only"),
        ),
        sa.CheckConstraint(
            "btrim(buyer_name) <> ''", name=op.f("ck_quotations_buyer_name_not_blank")
        ),
        sa.CheckConstraint(
            "currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1",
            name=op.f("ck_quotations_krw_fx_is_one"),
        ),
        sa.CheckConstraint(
            "doc_number ~ '^QT-[0-9]{4}-[0-9]{4,}$'", name=op.f("ck_quotations_doc_number_format")
        ),
        sa.CheckConstraint(
            "frozen_at IS NULL OR (payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL AND btrim(buyer_name) <> '' AND valid_until IS NOT NULL AND buyer_address IS NOT NULL AND btrim(buyer_address) <> '')",
            name=op.f("ck_quotations_frozen_complete"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DAT' OR incoterm_year = 2010",
            name=op.f("ck_quotations_incoterm_dat_2010"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DPU' OR incoterm_year = 2020",
            name=op.f("ck_quotations_incoterm_dpu_2020"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS NULL OR incoterm_code IN ('CFR', 'CIF', 'CIP', 'CPT', 'DAP', 'DAT', 'DDP', 'DPU', 'EXW', 'FAS', 'FCA', 'FOB')",
            name=op.f("ck_quotations_incoterm_code_valid"),
        ),
        sa.CheckConstraint(
            "incoterm_place IS NULL OR btrim(incoterm_place) <> ''",
            name=op.f("ck_quotations_incoterm_place_not_blank"),
        ),
        sa.CheckConstraint(
            "payment_type IS NULL OR payment_type IN ('LC', 'TT_ADVANCE', 'TT_DEFERRED')",
            name=op.f("ck_quotations_payment_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('CANCELLED', 'CONVERTED', 'DRAFT', 'EXPIRED', 'ISSUED')",
            name=op.f("ck_quotations_status_valid"),
        ),
        sa.CheckConstraint(
            "(fx_rate IS NULL) = (fx_rate_date IS NULL)", name=op.f("ck_quotations_fx_pair")
        ),
        sa.CheckConstraint(
            "(incoterm_code IS NULL AND incoterm_place IS NULL AND incoterm_year IS NULL) OR (incoterm_code IS NOT NULL AND incoterm_place IS NOT NULL AND incoterm_year IS NOT NULL)",
            name=op.f("ck_quotations_incoterm_all_or_none"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days BETWEEN -90 AND 365",
            name=op.f("ck_quotations_balance_days_range"),
        ),
        sa.CheckConstraint(
            "copied_from_id IS NULL OR copied_from_id <> id",
            name=op.f("ck_quotations_copied_from_not_self"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_quotations_currency_uppercase")
        ),
        sa.CheckConstraint(
            "fx_rate > 0 AND fx_rate <= 1000000", name=op.f("ck_quotations_fx_rate_range")
        ),
        sa.CheckConstraint(
            "fx_rate_date IS NULL OR fx_rate_date <= doc_date",
            name=op.f("ck_quotations_fx_date_not_future"),
        ),
        sa.CheckConstraint(
            "incoterm_year IS NULL OR incoterm_year IN (2010, 2020)",
            name=op.f("ck_quotations_incoterm_year_valid"),
        ),
        sa.CheckConstraint(
            "last_line_no >= 0", name=op.f("ck_quotations_last_line_no_nonnegative")
        ),
        sa.CheckConstraint(
            "total_amount BETWEEN 0 AND 9007199254740991", name=op.f("ck_quotations_total_range")
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= doc_date",
            name=op.f("ck_quotations_valid_until_after_doc"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_quotations_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_partner_id"],
            ["partners.id"],
            name=op.f("fk_quotations_buyer_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["quotations.id"],
            name=op.f("fk_quotations_copied_from_id_quotations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_quotations_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dest_market_code"],
            ["markets.code"],
            name=op.f("fk_quotations_dest_market_code_markets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_quotations_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotations")),
        sa.UniqueConstraint("doc_number", name="uq_quotations_doc_number"),
        sa.UniqueConstraint("id", "currency", name="uq_quotations_id_currency"),
    )
    op.create_index("ix_quotations_assignee_id", "quotations", ["assignee_id"], unique=False)
    op.create_index(
        "ix_quotations_buyer_partner_id_doc_date",
        "quotations",
        ["buyer_partner_id", "doc_date"],
        unique=False,
    )
    op.create_index(
        "ix_quotations_expiry",
        "quotations",
        ["valid_until"],
        unique=False,
        postgresql_where=sa.text("status = 'ISSUED' AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_quotations_status_doc_date", "quotations", ["status", "doc_date"], unique=False
    )
    op.create_index(
        "uq_quotations_copied_from_id_live",
        "quotations",
        ["copied_from_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.create_table(
        "quotation_status_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("quotation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "from_status IS NOT NULL OR to_status = 'DRAFT'",
            name=op.f("ck_quotation_status_log_birth_row"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('CANCELLED', 'CONVERTED', 'DRAFT', 'EXPIRED', 'ISSUED')",
            name=op.f("ck_quotation_status_log_from_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status IN ('CANCELLED', 'CONVERTED', 'DRAFT', 'EXPIRED', 'ISSUED')",
            name=op.f("ck_quotation_status_log_to_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status NOT IN ('CANCELLED', 'EXPIRED') OR reason IS NOT NULL",
            name=op.f("ck_quotation_status_log_reason_required"),
        ),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL OR automatic",
            name=op.f("ck_quotation_status_log_actor_or_automatic"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name=op.f("ck_quotation_status_log_no_self_transition"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500",
            name=op.f("ck_quotation_status_log_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_quotation_status_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quotation_id"],
            ["quotations.id"],
            name=op.f("fk_quotation_status_log_quotation_id_quotations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_status_log")),
    )
    op.create_index(
        "ix_quotation_status_log_quotation_id_id",
        "quotation_status_log",
        ["quotation_id", sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "uq_quotation_status_log_quotation_id_birth",
        "quotation_status_log",
        ["quotation_id"],
        unique=True,
        postgresql_where=sa.text("from_status IS NULL"),
    )
    revoke_mutations(op, "quotation_status_log")
    op.create_table(
        "quotation_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("qt_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(length=40), nullable=False),
        sa.Column("sku_name_ko", sa.String(length=200), nullable=False),
        sa.Column("sku_name_en", sa.String(length=200), nullable=True),
        sa.Column("sku_kind", sa.String(length=6), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("buyer_item_code", sa.String(length=100), nullable=True),
        sa.Column("unit_price_amount", sa.BigInteger(), nullable=False),
        sa.Column("list_price_amount", sa.BigInteger(), nullable=True),
        sa.Column("line_amount", sa.BigInteger(), nullable=False),
        sa.Column("price_basis", sa.String(length=10), nullable=False),
        sa.Column("is_free", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("price_reason", sa.String(length=200), nullable=True),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
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
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "NOT is_free OR (price_reason IS NOT NULL AND btrim(price_reason) <> '')",
            name=op.f("ck_quotation_lines_free_requires_reason"),
        ),
        sa.CheckConstraint(
            "NOT is_free OR price_basis IN ('MANUAL', 'BUYER_PO')",
            name=op.f("ck_quotation_lines_free_not_from_master"),
        ),
        sa.CheckConstraint(
            "price_basis IN ('BUYER_PO', 'MANUAL', 'MASTER')",
            name=op.f("ck_quotation_lines_price_basis_valid"),
        ),
        sa.CheckConstraint(
            "sku_kind IN ('SET', 'SINGLE')", name=op.f("ck_quotation_lines_sku_kind_valid")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_quotation_lines_currency_uppercase")
        ),
        sa.CheckConstraint(
            "is_free = (unit_price_amount = 0)", name=op.f("ck_quotation_lines_free_iff_zero_price")
        ),
        sa.CheckConstraint(
            "line_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_quotation_lines_line_amount_range"),
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_quotation_lines_line_no_positive")),
        sa.CheckConstraint(
            "list_price_amount IS NULL OR list_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_quotation_lines_list_price_range"),
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99999999", name=op.f("ck_quotation_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "quantity::numeric * unit_price_amount = line_amount",
            name=op.f("ck_quotation_lines_line_amount_matches"),
        ),
        sa.CheckConstraint(
            "unit_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_quotation_lines_unit_price_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_quotation_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qt_id", "currency"],
            ["quotations.id", "quotations.currency"],
            name=op.f("fk_quotation_lines_qt_id_currency_quotations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            name=op.f("fk_quotation_lines_sku_id_skus"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_quotation_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_quotation_lines")),
    )
    op.create_index("ix_quotation_lines_qt_id", "quotation_lines", ["qt_id"], unique=False)
    op.create_index("ix_quotation_lines_sku_id", "quotation_lines", ["sku_id"], unique=False)
    op.create_index(
        "uq_quotation_lines_qt_id_line_no_active",
        "quotation_lines",
        ["qt_id", "line_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_quotation_lines_qt_id_sku_id_is_free_active",
        "quotation_lines",
        ["qt_id", "sku_id", "is_free"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_quotation_lines_qt_id_sku_id_is_free_active",
        table_name="quotation_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_quotation_lines_qt_id_line_no_active",
        table_name="quotation_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_quotation_lines_sku_id", table_name="quotation_lines")
    op.drop_index("ix_quotation_lines_qt_id", table_name="quotation_lines")
    op.drop_table("quotation_lines")
    op.drop_index(
        "uq_quotation_status_log_quotation_id_birth",
        table_name="quotation_status_log",
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.drop_index("ix_quotation_status_log_quotation_id_id", table_name="quotation_status_log")
    op.drop_table("quotation_status_log")
    op.drop_index(
        "uq_quotations_copied_from_id_live",
        table_name="quotations",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.drop_index("ix_quotations_status_doc_date", table_name="quotations")
    op.drop_index(
        "ix_quotations_expiry",
        table_name="quotations",
        postgresql_where=sa.text("status = 'ISSUED' AND deleted_at IS NULL"),
    )
    op.drop_index("ix_quotations_buyer_partner_id_doc_date", table_name="quotations")
    op.drop_index("ix_quotations_assignee_id", table_name="quotations")
    op.drop_table("quotations")
