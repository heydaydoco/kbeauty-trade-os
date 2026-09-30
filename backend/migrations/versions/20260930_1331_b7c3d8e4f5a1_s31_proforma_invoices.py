"""s31_proforma_invoices

리비전 ID: b7c3d8e4f5a1
직전 리비전: a1905fcead96
생성 시각(UTC): 2026-09-30 13:31:08.924697+00:00

S3-1 PR-6a PI(선수금 청구서)·은행계좌 스키마 — M04 (ADR-0051~0056 / design-integrated §2.1·§2.11):
  ① bank_accounts — 자사 수취 계좌 마스터(쓰기 ADMIN 전용, 초기 행은 앱 경로 — 시드 0)
  ② proforma_invoices — PI 헤더(생성=발행=동결: frozen_at NOT NULL DEFAULT now(), qt_id NOT NULL,
     은행정보 6열 스냅샷, UNIQUE(id, currency)=라인 복합 FK 대상, UNIQUE(id, qt_id)=SO 복합 FK 대상[PR-7])
  ③ proforma_invoice_lines — PI 라인(자기 currency+헤더 복합 FK, qt_line_id NOT NULL=잔량 소비 관계, 복합 FK (pi_id,qt_id)·(qt_id,qt_line_id)로 원천 QT 라인의 소속을 DB가 보증)
  ⑤ ALTER quotation_lines ADD UNIQUE(qt_id, id) — 위 복합 FK의 대상(M03 파일 무수정)
  ④ proforma_invoice_status_log — PI 상태 변경 이력(IMMUTABLE — revoke_mutations)

체크리스트:
  ■ 신규 테이블만(백필·기존 테이블 변경 없음). CHECK는 create_table 안에 op.f() 최종 이름이다(함정 ①·⑪ —
    alembic check가 CHECK를 못 보므로 정의문 테스트 tests/integration/test_proforma_constraints.py가
    pg_get_constraintdef로 고정한다).
  ■ doc_number는 전역 UNIQUE(재발급 금지 §17.3), 라인 (pi_id, line_no)·(pi_id, sku_id, is_free)·bank label은
    unique_active 부분 인덱스(§17.4), 복제본 유일(copied_from_id)은 살아 있는 행 한정(X-08).
    **account_no·bank_account_no는 어떤 유니크 키에도 없다**(test_secret_boundaries).
  ■ **시드 0** — 전부 ActorMixin(users FK) 또는 앱 소유 마스터라 마이그레이션 시드 금지(함정 ⑩).
  ■ proforma_invoice_status_log는 REVOKE UPDATE, DELETE, TRUNCATE를 손으로 부른다(table_policy.IMMUTABLE_TABLES와 짝).
  ■ downgrade는 인덱스 → 테이블 역순 drop(REVOKE는 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations


revision: str = "b7c3d8e4f5a1"
down_revision: str | None = "a1905fcead96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기존 테이블(M03 quotation_lines)에는 UNIQUE 1건만 ALTER로 더한다 — PI 라인의 복합 FK `(qt_id, qt_line_id)` 대상.
    # (M03 파일은 건드리지 않는다. 데이터가 있어도 id가 PK라 (qt_id, id)는 항상 유일하므로 안전한 additive다.)
    op.create_unique_constraint("uq_quotation_lines_qt_id_id", "quotation_lines", ["qt_id", "id"])
    op.create_table(
        "bank_accounts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("beneficiary_name", sa.String(length=200), nullable=False),
        sa.Column("beneficiary_address", sa.String(length=300), nullable=False),
        sa.Column("bank_name", sa.String(length=200), nullable=False),
        sa.Column("bank_address", sa.String(length=300), nullable=False),
        sa.Column("account_no", sa.String(length=40), nullable=False),
        sa.Column("swift_code", sa.String(length=11), nullable=False),
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
            "btrim(label) <> '' AND btrim(beneficiary_name) <> '' AND btrim(beneficiary_address) <> '' AND btrim(bank_name) <> '' AND btrim(bank_address) <> '' AND btrim(account_no) <> ''",
            name=op.f("ck_bank_accounts_text_not_blank"),
        ),
        sa.CheckConstraint(
            "swift_code ~ '^[A-Z0-9]{8}([A-Z0-9]{3})?$'", name=op.f("ck_bank_accounts_swift_format")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_bank_accounts_currency_uppercase")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_bank_accounts_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_bank_accounts_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_accounts")),
    )
    op.create_index(
        "uq_bank_accounts_label_active",
        "bank_accounts",
        ["label"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "proforma_invoices",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("qt_id", sa.BigInteger(), nullable=False),
        sa.Column("buyer_address", sa.String(length=500), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_beneficiary_name", sa.String(length=200), nullable=False),
        sa.Column("bank_beneficiary_address", sa.String(length=300), nullable=False),
        sa.Column("bank_name", sa.String(length=200), nullable=False),
        sa.Column("bank_address", sa.String(length=300), nullable=False),
        sa.Column("bank_account_no", sa.String(length=40), nullable=False),
        sa.Column("bank_swift_code", sa.String(length=11), nullable=False),
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
            name=op.f("ck_proforma_invoices_payment_terms_shape"),
        ),
        sa.CheckConstraint(
            "balance_anchor IS NULL OR balance_anchor IN ('ARRIVAL_DATE', 'BL_DATE', 'ETD_DATE', 'INVOICE_DATE', 'ORDER_DATE', 'RECEIPT_DATE')",
            name=op.f("ck_proforma_invoices_balance_anchor_valid"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days >= 0 OR balance_anchor = 'ETD_DATE'",
            name=op.f("ck_proforma_invoices_neg_days_etd_only"),
        ),
        sa.CheckConstraint(
            "bank_swift_code ~ '^[A-Z0-9]{8}([A-Z0-9]{3})?$'",
            name=op.f("ck_proforma_invoices_bank_swift_format"),
        ),
        sa.CheckConstraint(
            "btrim(buyer_name) <> ''", name=op.f("ck_proforma_invoices_buyer_name_not_blank")
        ),
        sa.CheckConstraint(
            "currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1",
            name=op.f("ck_proforma_invoices_krw_fx_is_one"),
        ),
        sa.CheckConstraint(
            "doc_number ~ '^PI-[0-9]{4}-[0-9]{4,}$'",
            name=op.f("ck_proforma_invoices_doc_number_format"),
        ),
        sa.CheckConstraint(
            "frozen_at IS NULL OR (payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL AND btrim(buyer_name) <> '' AND btrim(buyer_address) <> '' AND btrim(bank_beneficiary_name) <> '' AND btrim(bank_beneficiary_address) <> '' AND btrim(bank_name) <> '' AND btrim(bank_address) <> '' AND btrim(bank_account_no) <> '')",
            name=op.f("ck_proforma_invoices_frozen_complete"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DAT' OR incoterm_year = 2010",
            name=op.f("ck_proforma_invoices_incoterm_dat_2010"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DPU' OR incoterm_year = 2020",
            name=op.f("ck_proforma_invoices_incoterm_dpu_2020"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS NULL OR incoterm_code IN ('CFR', 'CIF', 'CIP', 'CPT', 'DAP', 'DAT', 'DDP', 'DPU', 'EXW', 'FAS', 'FCA', 'FOB')",
            name=op.f("ck_proforma_invoices_incoterm_code_valid"),
        ),
        sa.CheckConstraint(
            "incoterm_place IS NULL OR btrim(incoterm_place) <> ''",
            name=op.f("ck_proforma_invoices_incoterm_place_not_blank"),
        ),
        sa.CheckConstraint(
            "payment_type IS NULL OR payment_type IN ('LC', 'TT_ADVANCE', 'TT_DEFERRED')",
            name=op.f("ck_proforma_invoices_payment_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('CANCELLED', 'EXPIRED', 'ISSUED', 'PAID', 'PARTIALLY_PAID')",
            name=op.f("ck_proforma_invoices_status_valid"),
        ),
        sa.CheckConstraint(
            "(fx_rate IS NULL) = (fx_rate_date IS NULL)", name=op.f("ck_proforma_invoices_fx_pair")
        ),
        sa.CheckConstraint(
            "(incoterm_code IS NULL AND incoterm_place IS NULL AND incoterm_year IS NULL) OR (incoterm_code IS NOT NULL AND incoterm_place IS NOT NULL AND incoterm_year IS NOT NULL)",
            name=op.f("ck_proforma_invoices_incoterm_all_or_none"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days BETWEEN -90 AND 365",
            name=op.f("ck_proforma_invoices_balance_days_range"),
        ),
        sa.CheckConstraint(
            "copied_from_id IS NULL OR copied_from_id <> id",
            name=op.f("ck_proforma_invoices_copied_from_not_self"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_proforma_invoices_currency_uppercase")
        ),
        sa.CheckConstraint(
            "fx_rate > 0 AND fx_rate <= 1000000", name=op.f("ck_proforma_invoices_fx_rate_range")
        ),
        sa.CheckConstraint(
            "fx_rate_date IS NULL OR fx_rate_date <= doc_date",
            name=op.f("ck_proforma_invoices_fx_date_not_future"),
        ),
        sa.CheckConstraint(
            "incoterm_year IS NULL OR incoterm_year IN (2010, 2020)",
            name=op.f("ck_proforma_invoices_incoterm_year_valid"),
        ),
        sa.CheckConstraint(
            "last_line_no >= 0", name=op.f("ck_proforma_invoices_last_line_no_nonnegative")
        ),
        sa.CheckConstraint(
            "total_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_proforma_invoices_total_range"),
        ),
        sa.CheckConstraint(
            "valid_until >= doc_date", name=op.f("ck_proforma_invoices_valid_until_after_doc")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_proforma_invoices_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["bank_accounts.id"],
            name=op.f("fk_proforma_invoices_bank_account_id_bank_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_partner_id"],
            ["partners.id"],
            name=op.f("fk_proforma_invoices_buyer_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["proforma_invoices.id"],
            name=op.f("fk_proforma_invoices_copied_from_id_proforma_invoices"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_proforma_invoices_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dest_market_code"],
            ["markets.code"],
            name=op.f("fk_proforma_invoices_dest_market_code_markets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qt_id"],
            ["quotations.id"],
            name=op.f("fk_proforma_invoices_qt_id_quotations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_proforma_invoices_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proforma_invoices")),
        sa.UniqueConstraint("doc_number", name="uq_proforma_invoices_doc_number"),
        sa.UniqueConstraint("id", "currency", name="uq_proforma_invoices_id_currency"),
        sa.UniqueConstraint("id", "qt_id", name="uq_proforma_invoices_id_qt_id"),
    )
    op.create_index(
        "ix_proforma_invoices_assignee_id", "proforma_invoices", ["assignee_id"], unique=False
    )
    op.create_index(
        "ix_proforma_invoices_bank_account_id",
        "proforma_invoices",
        ["bank_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_proforma_invoices_buyer_partner_id_doc_date",
        "proforma_invoices",
        ["buyer_partner_id", "doc_date"],
        unique=False,
    )
    op.create_index(
        "ix_proforma_invoices_expiry",
        "proforma_invoices",
        ["valid_until"],
        unique=False,
        postgresql_where=sa.text("status = 'ISSUED' AND deleted_at IS NULL"),
    )
    op.create_index("ix_proforma_invoices_qt_id", "proforma_invoices", ["qt_id"], unique=False)
    op.create_index(
        "ix_proforma_invoices_status_doc_date",
        "proforma_invoices",
        ["status", "doc_date"],
        unique=False,
    )
    op.create_index(
        "uq_proforma_invoices_copied_from_id_live",
        "proforma_invoices",
        ["copied_from_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.create_table(
        "proforma_invoice_status_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("proforma_invoice_id", sa.BigInteger(), nullable=False),
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
            "from_status IS NOT NULL OR to_status = 'ISSUED'",
            name=op.f("ck_proforma_invoice_status_log_birth_row"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('CANCELLED', 'EXPIRED', 'ISSUED', 'PAID', 'PARTIALLY_PAID')",
            name=op.f("ck_proforma_invoice_status_log_from_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status IN ('CANCELLED', 'EXPIRED', 'ISSUED', 'PAID', 'PARTIALLY_PAID')",
            name=op.f("ck_proforma_invoice_status_log_to_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status NOT IN ('CANCELLED', 'EXPIRED') OR reason IS NOT NULL",
            name=op.f("ck_proforma_invoice_status_log_reason_required"),
        ),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL OR automatic",
            name=op.f("ck_proforma_invoice_status_log_actor_or_automatic"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name=op.f("ck_proforma_invoice_status_log_no_self_transition"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500",
            name=op.f("ck_proforma_invoice_status_log_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_proforma_invoice_status_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proforma_invoice_id"],
            ["proforma_invoices.id"],
            name=op.f("fk_proforma_invoice_status_log_proforma_invoice_id_proforma_invoices"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proforma_invoice_status_log")),
    )
    op.create_index(
        "ix_proforma_invoice_status_log_proforma_invoice_id_id",
        "proforma_invoice_status_log",
        ["proforma_invoice_id", sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "uq_proforma_invoice_status_log_proforma_invoice_id_birth",
        "proforma_invoice_status_log",
        ["proforma_invoice_id"],
        unique=True,
        postgresql_where=sa.text("from_status IS NULL"),
    )
    revoke_mutations(op, "proforma_invoice_status_log")
    op.create_table(
        "proforma_invoice_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("pi_id", sa.BigInteger(), nullable=False),
        sa.Column("qt_id", sa.BigInteger(), nullable=False),
        sa.Column("qt_line_id", sa.BigInteger(), nullable=False),
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
            name=op.f("ck_proforma_invoice_lines_free_requires_reason"),
        ),
        sa.CheckConstraint(
            "NOT is_free OR price_basis IN ('MANUAL', 'BUYER_PO')",
            name=op.f("ck_proforma_invoice_lines_free_not_from_master"),
        ),
        sa.CheckConstraint(
            "price_basis IN ('BUYER_PO', 'MANUAL', 'MASTER')",
            name=op.f("ck_proforma_invoice_lines_price_basis_valid"),
        ),
        sa.CheckConstraint(
            "sku_kind IN ('SET', 'SINGLE')", name=op.f("ck_proforma_invoice_lines_sku_kind_valid")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_proforma_invoice_lines_currency_uppercase")
        ),
        sa.CheckConstraint(
            "is_free = (unit_price_amount = 0)",
            name=op.f("ck_proforma_invoice_lines_free_iff_zero_price"),
        ),
        sa.CheckConstraint(
            "line_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_proforma_invoice_lines_line_amount_range"),
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_proforma_invoice_lines_line_no_positive")),
        sa.CheckConstraint(
            "list_price_amount IS NULL OR list_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_proforma_invoice_lines_list_price_range"),
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99999999", name=op.f("ck_proforma_invoice_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "quantity::numeric * unit_price_amount = line_amount",
            name=op.f("ck_proforma_invoice_lines_line_amount_matches"),
        ),
        sa.CheckConstraint(
            "unit_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_proforma_invoice_lines_unit_price_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_proforma_invoice_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pi_id", "currency"],
            ["proforma_invoices.id", "proforma_invoices.currency"],
            name=op.f("fk_proforma_invoice_lines_pi_id_currency_proforma_invoices"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pi_id", "qt_id"],
            ["proforma_invoices.id", "proforma_invoices.qt_id"],
            name=op.f("fk_proforma_invoice_lines_pi_id_qt_id_proforma_invoices"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qt_id", "qt_line_id"],
            ["quotation_lines.qt_id", "quotation_lines.id"],
            name=op.f("fk_proforma_invoice_lines_qt_id_qt_line_id_quotation_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            name=op.f("fk_proforma_invoice_lines_sku_id_skus"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_proforma_invoice_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proforma_invoice_lines")),
    )
    op.create_index(
        "ix_proforma_invoice_lines_pi_id", "proforma_invoice_lines", ["pi_id"], unique=False
    )
    op.create_index(
        "ix_proforma_invoice_lines_qt_line_id",
        "proforma_invoice_lines",
        ["qt_line_id"],
        unique=False,
    )
    op.create_index(
        "ix_proforma_invoice_lines_sku_id", "proforma_invoice_lines", ["sku_id"], unique=False
    )
    op.create_index(
        "uq_proforma_invoice_lines_pi_id_line_no_active",
        "proforma_invoice_lines",
        ["pi_id", "line_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_proforma_invoice_lines_pi_id_sku_id_is_free_active",
        "proforma_invoice_lines",
        ["pi_id", "sku_id", "is_free"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_proforma_invoice_lines_pi_id_sku_id_is_free_active",
        table_name="proforma_invoice_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_proforma_invoice_lines_pi_id_line_no_active",
        table_name="proforma_invoice_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_proforma_invoice_lines_sku_id", table_name="proforma_invoice_lines")
    op.drop_index("ix_proforma_invoice_lines_qt_line_id", table_name="proforma_invoice_lines")
    op.drop_index("ix_proforma_invoice_lines_pi_id", table_name="proforma_invoice_lines")
    op.drop_table("proforma_invoice_lines")
    op.drop_index(
        "uq_proforma_invoice_status_log_proforma_invoice_id_birth",
        table_name="proforma_invoice_status_log",
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.drop_index(
        "ix_proforma_invoice_status_log_proforma_invoice_id_id",
        table_name="proforma_invoice_status_log",
    )
    op.drop_table("proforma_invoice_status_log")
    op.drop_index(
        "uq_proforma_invoices_copied_from_id_live",
        table_name="proforma_invoices",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.drop_index("ix_proforma_invoices_status_doc_date", table_name="proforma_invoices")
    op.drop_index("ix_proforma_invoices_qt_id", table_name="proforma_invoices")
    op.drop_index(
        "ix_proforma_invoices_expiry",
        table_name="proforma_invoices",
        postgresql_where=sa.text("status = 'ISSUED' AND deleted_at IS NULL"),
    )
    op.drop_index("ix_proforma_invoices_buyer_partner_id_doc_date", table_name="proforma_invoices")
    op.drop_index("ix_proforma_invoices_bank_account_id", table_name="proforma_invoices")
    op.drop_index("ix_proforma_invoices_assignee_id", table_name="proforma_invoices")
    op.drop_table("proforma_invoices")
    op.drop_index(
        "uq_bank_accounts_label_active",
        table_name="bank_accounts",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("bank_accounts")
    op.drop_constraint("uq_quotation_lines_qt_id_id", "quotation_lines", type_="unique")
