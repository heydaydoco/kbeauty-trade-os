"""s31_sales_orders

리비전 ID: 057015346693
직전 리비전: b7c3d8e4f5a1
생성 시각(UTC): 2026-09-30 16:17:06.075533+00:00

S3-1 PR-7a 수주(SO) 스키마 — M05 (ADR-0051~0053 / design-integrated §2.1·§2.11 / design-A A2·A4 / design-B B1·B2·B6):
  ① sales_orders — SO 헤더(RECEIVED에서 시작, 동결 표식은 confirmed_at 하나[X-03], 거래처·참조 FK·복제 원본은 ORIGIN 불변,
     복합 FK (pi_id, qt_id)→proforma_invoices(id, qt_id) + CHECK pi_requires_qt(MATCH SIMPLE 공백 보강),
     buyer_po_no·buyer_po_no_key 쌍 CHECK, UNIQUE(id, currency)=라인 복합 FK 대상)
     · **중복 바이어 PO 0건** = 부분 유니크 uq_sales_orders_buyer_po_live (buyer_partner_id, buyer_po_no_key) —
       살아 있고 CANCELLED 아닌 행 한정(취소 SO는 번호를 점유하지 않는다 — 정정 = 취소+신규)
     · PI→SO 활성 1:1 = 부분 유니크 uq_sales_orders_pi_id_live, 살아 있는 복제본 유일(copied_from_id, X-08)
     · confirmed_at 결속 CHECK(RECEIVED→NULL, CONFIRMED·후반→NOT NULL, ON_HOLD·CANCELLED 무제약)
  ② sales_order_lines — SO 라인(자기 currency+헤더 복합 FK, qt_line_id·pi_line_id는 num_nonnulls<=1, requested_delivery_date)
  ③ sales_order_status_log — SO 상태 변경 이력(IMMUTABLE — revoke_mutations). approval_id는 승인 코어 뒤 M10이 ADD.

체크리스트:
  ■ 신규 테이블만(백필·기존 테이블 변경 없음). CHECK는 create_table 안에 op.f() 최종 이름이다(함정 ①·⑪ —
    alembic check가 CHECK를 못 보므로 정의문 테스트 tests/integration/test_sales_order_constraints.py가
    pg_get_constraintdef·pg_get_indexdef로 고정한다).
  ■ doc_number는 전역 UNIQUE(재발급 금지 §17.3), 라인 (so_id, line_no)·(so_id, sku_id, is_free)는 unique_active 부분 인덱스(§17.4).
  ■ **시드 0** — 전부 ActorMixin(users FK) 소유 표라 마이그레이션 시드 금지(함정 ⑩).
  ■ sales_order_status_log는 REVOKE UPDATE, DELETE, TRUNCATE를 손으로 부른다(table_policy.IMMUTABLE_TABLES와 짝).
  ■ 확정 증적 3열(credit_verdict·credit_approval_id·pi_gate_verdict)·open_exposure 인덱스·상태이력 approval_id는 M10(PR-12) 몫.
  ■ downgrade는 인덱스 → 테이블 역순 drop(REVOKE는 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations


revision: str = "057015346693"
down_revision: str | None = "b7c3d8e4f5a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("qt_id", sa.BigInteger(), nullable=True),
        sa.Column("pi_id", sa.BigInteger(), nullable=True),
        sa.Column("buyer_po_no", sa.String(length=60), nullable=True),
        sa.Column("buyer_po_no_key", sa.String(length=60), nullable=True),
        sa.Column("buyer_po_date", sa.Date(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("ck_sales_orders_payment_terms_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'RECEIVED' AND confirmed_at IS NULL) OR (status IN ('CONFIRMED', 'PARTIALLY_ALLOCATED', 'ALLOCATED', 'IN_SHIPMENT', 'COMPLETED') AND confirmed_at IS NOT NULL) OR status IN ('ON_HOLD', 'CANCELLED')",
            name=op.f("ck_sales_orders_confirmed_at_consistent"),
        ),
        sa.CheckConstraint(
            "balance_anchor IS NULL OR balance_anchor IN ('ARRIVAL_DATE', 'BL_DATE', 'ETD_DATE', 'INVOICE_DATE', 'ORDER_DATE', 'RECEIPT_DATE')",
            name=op.f("ck_sales_orders_balance_anchor_valid"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days >= 0 OR balance_anchor = 'ETD_DATE'",
            name=op.f("ck_sales_orders_neg_days_etd_only"),
        ),
        sa.CheckConstraint(
            "btrim(buyer_name) <> ''", name=op.f("ck_sales_orders_buyer_name_not_blank")
        ),
        sa.CheckConstraint(
            "buyer_po_no IS NULL OR (btrim(buyer_po_no) <> '' AND btrim(buyer_po_no_key) <> '')",
            name=op.f("ck_sales_orders_po_not_blank"),
        ),
        sa.CheckConstraint(
            "confirmed_at IS NULL OR (payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL AND btrim(buyer_name) <> '')",
            name=op.f("ck_sales_orders_frozen_complete"),
        ),
        sa.CheckConstraint(
            "currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1",
            name=op.f("ck_sales_orders_krw_fx_is_one"),
        ),
        sa.CheckConstraint(
            "doc_number ~ '^SO-[0-9]{4}-[0-9]{4,}$'", name=op.f("ck_sales_orders_doc_number_format")
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DAT' OR incoterm_year = 2010",
            name=op.f("ck_sales_orders_incoterm_dat_2010"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DPU' OR incoterm_year = 2020",
            name=op.f("ck_sales_orders_incoterm_dpu_2020"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS NULL OR incoterm_code IN ('CFR', 'CIF', 'CIP', 'CPT', 'DAP', 'DAT', 'DDP', 'DPU', 'EXW', 'FAS', 'FCA', 'FOB')",
            name=op.f("ck_sales_orders_incoterm_code_valid"),
        ),
        sa.CheckConstraint(
            "incoterm_place IS NULL OR btrim(incoterm_place) <> ''",
            name=op.f("ck_sales_orders_incoterm_place_not_blank"),
        ),
        sa.CheckConstraint(
            "payment_type IS NULL OR payment_type IN ('LC', 'TT_ADVANCE', 'TT_DEFERRED')",
            name=op.f("ck_sales_orders_payment_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('ALLOCATED', 'CANCELLED', 'COMPLETED', 'CONFIRMED', 'IN_SHIPMENT', 'ON_HOLD', 'PARTIALLY_ALLOCATED', 'RECEIVED')",
            name=op.f("ck_sales_orders_status_valid"),
        ),
        sa.CheckConstraint(
            "(buyer_po_no IS NULL) = (buyer_po_no_key IS NULL)",
            name=op.f("ck_sales_orders_po_pair"),
        ),
        sa.CheckConstraint(
            "(fx_rate IS NULL) = (fx_rate_date IS NULL)", name=op.f("ck_sales_orders_fx_pair")
        ),
        sa.CheckConstraint(
            "(incoterm_code IS NULL AND incoterm_place IS NULL AND incoterm_year IS NULL) OR (incoterm_code IS NOT NULL AND incoterm_place IS NOT NULL AND incoterm_year IS NOT NULL)",
            name=op.f("ck_sales_orders_incoterm_all_or_none"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days BETWEEN -90 AND 365",
            name=op.f("ck_sales_orders_balance_days_range"),
        ),
        sa.CheckConstraint(
            "copied_from_id IS NULL OR copied_from_id <> id",
            name=op.f("ck_sales_orders_copied_from_not_self"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_sales_orders_currency_uppercase")
        ),
        sa.CheckConstraint(
            "fx_rate > 0 AND fx_rate <= 1000000", name=op.f("ck_sales_orders_fx_rate_range")
        ),
        sa.CheckConstraint(
            "fx_rate_date IS NULL OR fx_rate_date <= doc_date",
            name=op.f("ck_sales_orders_fx_date_not_future"),
        ),
        sa.CheckConstraint(
            "incoterm_year IS NULL OR incoterm_year IN (2010, 2020)",
            name=op.f("ck_sales_orders_incoterm_year_valid"),
        ),
        sa.CheckConstraint(
            "last_line_no >= 0", name=op.f("ck_sales_orders_last_line_no_nonnegative")
        ),
        sa.CheckConstraint(
            "pi_id IS NULL OR qt_id IS NOT NULL", name=op.f("ck_sales_orders_pi_requires_qt")
        ),
        sa.CheckConstraint(
            "total_amount BETWEEN 0 AND 9007199254740991", name=op.f("ck_sales_orders_total_range")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_sales_orders_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_partner_id"],
            ["partners.id"],
            name=op.f("fk_sales_orders_buyer_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["sales_orders.id"],
            name=op.f("fk_sales_orders_copied_from_id_sales_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_sales_orders_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dest_market_code"],
            ["markets.code"],
            name=op.f("fk_sales_orders_dest_market_code_markets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pi_id", "qt_id"],
            ["proforma_invoices.id", "proforma_invoices.qt_id"],
            name=op.f("fk_sales_orders_pi_id_qt_id_proforma_invoices"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qt_id"],
            ["quotations.id"],
            name=op.f("fk_sales_orders_qt_id_quotations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_sales_orders_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_orders")),
        sa.UniqueConstraint("doc_number", name="uq_sales_orders_doc_number"),
        sa.UniqueConstraint("id", "currency", name="uq_sales_orders_id_currency"),
    )
    op.create_index("ix_sales_orders_assignee_id", "sales_orders", ["assignee_id"], unique=False)
    op.create_index(
        "ix_sales_orders_buyer_partner_id_doc_date",
        "sales_orders",
        ["buyer_partner_id", "doc_date"],
        unique=False,
    )
    op.create_index("ix_sales_orders_pi_id", "sales_orders", ["pi_id"], unique=False)
    op.create_index("ix_sales_orders_qt_id", "sales_orders", ["qt_id"], unique=False)
    op.create_index(
        "ix_sales_orders_status_doc_date", "sales_orders", ["status", "doc_date"], unique=False
    )
    op.create_index(
        "uq_sales_orders_buyer_po_live",
        "sales_orders",
        ["buyer_partner_id", "buyer_po_no_key"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND buyer_po_no_key IS NOT NULL AND status <> 'CANCELLED'"
        ),
    )
    op.create_index(
        "uq_sales_orders_copied_from_id_live",
        "sales_orders",
        ["copied_from_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.create_index(
        "uq_sales_orders_pi_id_live",
        "sales_orders",
        ["pi_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND pi_id IS NOT NULL AND status <> 'CANCELLED'"
        ),
    )
    op.create_table(
        "sales_order_status_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("sales_order_id", sa.BigInteger(), nullable=False),
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
            "from_status IS NOT NULL OR to_status = 'RECEIVED'",
            name=op.f("ck_sales_order_status_log_birth_row"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('ALLOCATED', 'CANCELLED', 'COMPLETED', 'CONFIRMED', 'IN_SHIPMENT', 'ON_HOLD', 'PARTIALLY_ALLOCATED', 'RECEIVED')",
            name=op.f("ck_sales_order_status_log_from_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status IN ('ALLOCATED', 'CANCELLED', 'COMPLETED', 'CONFIRMED', 'IN_SHIPMENT', 'ON_HOLD', 'PARTIALLY_ALLOCATED', 'RECEIVED')",
            name=op.f("ck_sales_order_status_log_to_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status NOT IN ('CANCELLED', 'ON_HOLD') OR reason IS NOT NULL",
            name=op.f("ck_sales_order_status_log_reason_required"),
        ),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL OR automatic",
            name=op.f("ck_sales_order_status_log_actor_or_automatic"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name=op.f("ck_sales_order_status_log_no_self_transition"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500",
            name=op.f("ck_sales_order_status_log_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_sales_order_status_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sales_order_id"],
            ["sales_orders.id"],
            name=op.f("fk_sales_order_status_log_sales_order_id_sales_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_order_status_log")),
    )
    op.create_index(
        "ix_sales_order_status_log_sales_order_id_id",
        "sales_order_status_log",
        ["sales_order_id", sa.literal_column("id DESC")],
        unique=False,
    )
    revoke_mutations(op, "sales_order_status_log")
    op.create_index(
        "uq_sales_order_status_log_sales_order_id_birth",
        "sales_order_status_log",
        ["sales_order_id"],
        unique=True,
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.create_table(
        "sales_order_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("so_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("qt_line_id", sa.BigInteger(), nullable=True),
        sa.Column("pi_line_id", sa.BigInteger(), nullable=True),
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
            name=op.f("ck_sales_order_lines_free_requires_reason"),
        ),
        sa.CheckConstraint(
            "NOT is_free OR price_basis IN ('MANUAL', 'BUYER_PO')",
            name=op.f("ck_sales_order_lines_free_not_from_master"),
        ),
        sa.CheckConstraint(
            "price_basis IN ('BUYER_PO', 'MANUAL', 'MASTER')",
            name=op.f("ck_sales_order_lines_price_basis_valid"),
        ),
        sa.CheckConstraint(
            "sku_kind IN ('SET', 'SINGLE')", name=op.f("ck_sales_order_lines_sku_kind_valid")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_sales_order_lines_currency_uppercase")
        ),
        sa.CheckConstraint(
            "is_free = (unit_price_amount = 0)",
            name=op.f("ck_sales_order_lines_free_iff_zero_price"),
        ),
        sa.CheckConstraint(
            "line_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_sales_order_lines_line_amount_range"),
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_sales_order_lines_line_no_positive")),
        sa.CheckConstraint(
            "list_price_amount IS NULL OR list_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_sales_order_lines_list_price_range"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(qt_line_id, pi_line_id) <= 1",
            name=op.f("ck_sales_order_lines_one_source_only"),
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99999999", name=op.f("ck_sales_order_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "quantity::numeric * unit_price_amount = line_amount",
            name=op.f("ck_sales_order_lines_line_amount_matches"),
        ),
        sa.CheckConstraint(
            "unit_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_sales_order_lines_unit_price_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_sales_order_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pi_line_id"],
            ["proforma_invoice_lines.id"],
            name=op.f("fk_sales_order_lines_pi_line_id_proforma_invoice_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qt_line_id"],
            ["quotation_lines.id"],
            name=op.f("fk_sales_order_lines_qt_line_id_quotation_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            name=op.f("fk_sales_order_lines_sku_id_skus"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["so_id", "currency"],
            ["sales_orders.id", "sales_orders.currency"],
            name=op.f("fk_sales_order_lines_so_id_currency_sales_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_sales_order_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_order_lines")),
    )
    op.create_index(
        "ix_sales_order_lines_pi_line_id", "sales_order_lines", ["pi_line_id"], unique=False
    )
    op.create_index(
        "ix_sales_order_lines_qt_line_id", "sales_order_lines", ["qt_line_id"], unique=False
    )
    op.create_index("ix_sales_order_lines_sku_id", "sales_order_lines", ["sku_id"], unique=False)
    op.create_index("ix_sales_order_lines_so_id", "sales_order_lines", ["so_id"], unique=False)
    op.create_index(
        "uq_sales_order_lines_so_id_line_no_active",
        "sales_order_lines",
        ["so_id", "line_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_sales_order_lines_so_id_sku_id_is_free_active",
        "sales_order_lines",
        ["so_id", "sku_id", "is_free"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_sales_order_lines_so_id_sku_id_is_free_active",
        table_name="sales_order_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_sales_order_lines_so_id_line_no_active",
        table_name="sales_order_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_sales_order_lines_so_id", table_name="sales_order_lines")
    op.drop_index("ix_sales_order_lines_sku_id", table_name="sales_order_lines")
    op.drop_index("ix_sales_order_lines_qt_line_id", table_name="sales_order_lines")
    op.drop_index("ix_sales_order_lines_pi_line_id", table_name="sales_order_lines")
    op.drop_table("sales_order_lines")
    op.drop_index(
        "uq_sales_order_status_log_sales_order_id_birth",
        table_name="sales_order_status_log",
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.drop_index(
        "ix_sales_order_status_log_sales_order_id_id", table_name="sales_order_status_log"
    )
    op.drop_table("sales_order_status_log")
    op.drop_index(
        "uq_sales_orders_pi_id_live",
        table_name="sales_orders",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND pi_id IS NOT NULL AND status <> 'CANCELLED'"
        ),
    )
    op.drop_index(
        "uq_sales_orders_copied_from_id_live",
        table_name="sales_orders",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.drop_index(
        "uq_sales_orders_buyer_po_live",
        table_name="sales_orders",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND buyer_po_no_key IS NOT NULL AND status <> 'CANCELLED'"
        ),
    )
    op.drop_index("ix_sales_orders_status_doc_date", table_name="sales_orders")
    op.drop_index("ix_sales_orders_qt_id", table_name="sales_orders")
    op.drop_index("ix_sales_orders_pi_id", table_name="sales_orders")
    op.drop_index("ix_sales_orders_buyer_partner_id_doc_date", table_name="sales_orders")
    op.drop_index("ix_sales_orders_assignee_id", table_name="sales_orders")
    op.drop_table("sales_orders")
