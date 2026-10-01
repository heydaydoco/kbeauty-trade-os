"""s31_purchase_orders

리비전 ID: a4c0e7fd37db
직전 리비전: 057015346693
생성 시각(UTC): 2026-09-30 22:15:59.490798+00:00

S3-1 PR-8a 구매 발주서(PO) 스키마 — M06 (ADR-0057·0051~0053 / design-integrated §2.1·§2.11 / design-A A12 / design-B B1·B2·B6 / design-F F1~F5):
  ① purchase_orders — PO 헤더(ISSUED에서 시작 — **초안 없음, 생성=발행=동결**: frozen_at NOT NULL DEFAULT now()[X-03],
     공급사·복제 원본은 ORIGIN 불변, **원가 열은 `_cost` 접미**[total_cost — 로그 마스킹·금액 판정 자동 편입], po_kind VARCHAR(16) NOT NULL DEFAULT 'PURCHASE'
     + CHECK po_kind_valid, OC 부속 열[oc_received_on·oc_reference] + CHECK 3종[발행 중 비어 있음·공급사 확인 이후 OC 일자 필수·OC ≥ 증빙일],
     UNIQUE(id, currency)=라인 복합 FK 대상, 살아 있는 복제본 유일[X-08])
  ② purchase_order_lines — PO 라인(SKU 전용 sku_id NOT NULL, 자기 currency+헤더 복합 FK, unit_cost ≥ 1[무상 매입 미지원]·
     line_cost=quantity×unit_cost numeric 곱 CHECK, price_basis ∈ {MASTER, MANUAL}, (po_id, sku_id) 활성 유일)
  ③ purchase_order_status_log — PO 상태 변경 이력(IMMUTABLE — revoke_mutations).

체크리스트:
  ■ M01에만 의존한다(partners·skus·users FK) — 승인·게이트·여신과 무관.
  ■ 신규 테이블만(백필·기존 테이블 변경 없음). CHECK는 create_table 안에 op.f() 최종 이름이다(함정 ①·⑪ — alembic check가 CHECK를 못 보므로
    정의문 테스트 tests/integration/test_purchase_order_constraints.py가 pg_get_constraintdef·pg_get_indexdef로 고정한다).
  ■ doc_number는 전역 UNIQUE(재발급 금지 §17.3), 라인 (po_id, line_no)·(po_id, sku_id)는 unique_active 부분 인덱스(§17.4).
  ■ **시드 0** — 전부 ActorMixin(users FK) 소유 표라 마이그레이션 시드 금지(함정 ⑩).
  ■ purchase_order_status_log는 REVOKE UPDATE, DELETE, TRUNCATE를 손으로 부른다(table_policy.IMMUTABLE_TABLES와 짝).
  ■ downgrade는 인덱스 → 테이블 역순 drop(REVOKE는 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations


revision: str = "a4c0e7fd37db"
down_revision: str | None = "057015346693"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("total_cost", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("supplier_partner_id", sa.BigInteger(), nullable=False),
        sa.Column("supplier_name", sa.String(length=200), nullable=False),
        sa.Column(
            "po_kind", sa.String(length=16), server_default=sa.text("'PURCHASE'"), nullable=False
        ),
        sa.Column("oc_received_on", sa.Date(), nullable=True),
        sa.Column("oc_reference", sa.String(length=100), nullable=True),
        sa.Column(
            "frozen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
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
            name=op.f("ck_purchase_orders_payment_terms_shape"),
        ),
        sa.CheckConstraint(
            "balance_anchor IS NULL OR balance_anchor IN ('ARRIVAL_DATE', 'BL_DATE', 'ETD_DATE', 'INVOICE_DATE', 'ORDER_DATE', 'RECEIPT_DATE')",
            name=op.f("ck_purchase_orders_balance_anchor_valid"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days >= 0 OR balance_anchor = 'ETD_DATE'",
            name=op.f("ck_purchase_orders_neg_days_etd_only"),
        ),
        sa.CheckConstraint(
            "btrim(supplier_name) <> ''", name=op.f("ck_purchase_orders_supplier_name_not_blank")
        ),
        sa.CheckConstraint(
            "currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1",
            name=op.f("ck_purchase_orders_krw_fx_is_one"),
        ),
        sa.CheckConstraint(
            "doc_number ~ '^PO-[0-9]{4}-[0-9]{4,}$'",
            name=op.f("ck_purchase_orders_doc_number_format"),
        ),
        sa.CheckConstraint(
            "frozen_at IS NULL OR (payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL AND btrim(supplier_name) <> '')",
            name=op.f("ck_purchase_orders_frozen_complete"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DAT' OR incoterm_year = 2010",
            name=op.f("ck_purchase_orders_incoterm_dat_2010"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DPU' OR incoterm_year = 2020",
            name=op.f("ck_purchase_orders_incoterm_dpu_2020"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS NULL OR incoterm_code IN ('CFR', 'CIF', 'CIP', 'CPT', 'DAP', 'DAT', 'DDP', 'DPU', 'EXW', 'FAS', 'FCA', 'FOB')",
            name=op.f("ck_purchase_orders_incoterm_code_valid"),
        ),
        sa.CheckConstraint(
            "incoterm_place IS NULL OR btrim(incoterm_place) <> ''",
            name=op.f("ck_purchase_orders_incoterm_place_not_blank"),
        ),
        sa.CheckConstraint(
            "oc_reference IS NULL OR btrim(oc_reference) <> ''",
            name=op.f("ck_purchase_orders_oc_reference_not_blank"),
        ),
        sa.CheckConstraint(
            "payment_type IS NULL OR payment_type IN ('LC', 'TT_ADVANCE', 'TT_DEFERRED')",
            name=op.f("ck_purchase_orders_payment_type_valid"),
        ),
        sa.CheckConstraint(
            "po_kind IN ('OEM_PRODUCTION', 'PURCHASE')",
            name=op.f("ck_purchase_orders_po_kind_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'ISSUED' OR (oc_received_on IS NULL AND oc_reference IS NULL)",
            name=op.f("ck_purchase_orders_oc_empty_when_issued"),
        ),
        sa.CheckConstraint(
            "status IN ('CANCELLED', 'CLOSED', 'FULLY_RECEIVED', 'ISSUED', 'PARTIALLY_RECEIVED', 'SUPPLIER_CONFIRMED')",
            name=op.f("ck_purchase_orders_status_valid"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('SUPPLIER_CONFIRMED', 'PARTIALLY_RECEIVED', 'FULLY_RECEIVED', 'CLOSED') OR oc_received_on IS NOT NULL",
            name=op.f("ck_purchase_orders_oc_required_after_confirm"),
        ),
        sa.CheckConstraint(
            "(fx_rate IS NULL) = (fx_rate_date IS NULL)", name=op.f("ck_purchase_orders_fx_pair")
        ),
        sa.CheckConstraint(
            "(incoterm_code IS NULL AND incoterm_place IS NULL AND incoterm_year IS NULL) OR (incoterm_code IS NOT NULL AND incoterm_place IS NOT NULL AND incoterm_year IS NOT NULL)",
            name=op.f("ck_purchase_orders_incoterm_all_or_none"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days BETWEEN -90 AND 365",
            name=op.f("ck_purchase_orders_balance_days_range"),
        ),
        sa.CheckConstraint(
            "copied_from_id IS NULL OR copied_from_id <> id",
            name=op.f("ck_purchase_orders_copied_from_not_self"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_purchase_orders_currency_uppercase")
        ),
        sa.CheckConstraint(
            "fx_rate > 0 AND fx_rate <= 1000000", name=op.f("ck_purchase_orders_fx_rate_range")
        ),
        sa.CheckConstraint(
            "fx_rate_date IS NULL OR fx_rate_date <= doc_date",
            name=op.f("ck_purchase_orders_fx_date_not_future"),
        ),
        sa.CheckConstraint(
            "incoterm_year IS NULL OR incoterm_year IN (2010, 2020)",
            name=op.f("ck_purchase_orders_incoterm_year_valid"),
        ),
        sa.CheckConstraint(
            "last_line_no >= 0", name=op.f("ck_purchase_orders_last_line_no_nonnegative")
        ),
        sa.CheckConstraint(
            "oc_received_on IS NULL OR oc_received_on >= doc_date",
            name=op.f("ck_purchase_orders_oc_not_before_doc"),
        ),
        sa.CheckConstraint(
            "total_cost BETWEEN 0 AND 9007199254740991", name=op.f("ck_purchase_orders_total_range")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_purchase_orders_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["purchase_orders.id"],
            name=op.f("fk_purchase_orders_copied_from_id_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_purchase_orders_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supplier_partner_id"],
            ["partners.id"],
            name=op.f("fk_purchase_orders_supplier_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_purchase_orders_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_orders")),
        sa.UniqueConstraint("doc_number", name="uq_purchase_orders_doc_number"),
        sa.UniqueConstraint("id", "currency", name="uq_purchase_orders_id_currency"),
    )
    op.create_index(
        "ix_purchase_orders_assignee_id", "purchase_orders", ["assignee_id"], unique=False
    )
    op.create_index(
        "ix_purchase_orders_status_doc_date",
        "purchase_orders",
        ["status", "doc_date"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_orders_supplier_partner_id_doc_date",
        "purchase_orders",
        ["supplier_partner_id", "doc_date"],
        unique=False,
    )
    op.create_index(
        "uq_purchase_orders_copied_from_id_live",
        "purchase_orders",
        ["copied_from_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.create_table(
        "purchase_order_status_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), nullable=False),
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
            name=op.f("ck_purchase_order_status_log_birth_row"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('CANCELLED', 'CLOSED', 'FULLY_RECEIVED', 'ISSUED', 'PARTIALLY_RECEIVED', 'SUPPLIER_CONFIRMED')",
            name=op.f("ck_purchase_order_status_log_from_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status IN ('CANCELLED', 'CLOSED', 'FULLY_RECEIVED', 'ISSUED', 'PARTIALLY_RECEIVED', 'SUPPLIER_CONFIRMED')",
            name=op.f("ck_purchase_order_status_log_to_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status NOT IN ('CANCELLED') OR reason IS NOT NULL",
            name=op.f("ck_purchase_order_status_log_reason_required"),
        ),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL OR automatic",
            name=op.f("ck_purchase_order_status_log_actor_or_automatic"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name=op.f("ck_purchase_order_status_log_no_self_transition"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500",
            name=op.f("ck_purchase_order_status_log_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_purchase_order_status_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_orders.id"],
            name=op.f("fk_purchase_order_status_log_purchase_order_id_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_status_log")),
    )
    op.create_index(
        "ix_purchase_order_status_log_purchase_order_id_id",
        "purchase_order_status_log",
        ["purchase_order_id", sa.literal_column("id DESC")],
        unique=False,
    )
    revoke_mutations(op, "purchase_order_status_log")
    op.create_index(
        "uq_purchase_order_status_log_purchase_order_id_birth",
        "purchase_order_status_log",
        ["purchase_order_id"],
        unique=True,
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.create_table(
        "purchase_order_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("po_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(length=40), nullable=False),
        sa.Column("sku_name_ko", sa.String(length=200), nullable=False),
        sa.Column("sku_name_en", sa.String(length=200), nullable=True),
        sa.Column("sku_kind", sa.String(length=6), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("unit_cost", sa.BigInteger(), nullable=False),
        sa.Column("line_cost", sa.BigInteger(), nullable=False),
        sa.Column("price_basis", sa.String(length=10), nullable=False),
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
            "price_basis IN ('MANUAL', 'MASTER')",
            name=op.f("ck_purchase_order_lines_price_basis_valid"),
        ),
        sa.CheckConstraint(
            "sku_kind IN ('SET', 'SINGLE')", name=op.f("ck_purchase_order_lines_sku_kind_valid")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_purchase_order_lines_currency_uppercase")
        ),
        sa.CheckConstraint(
            "line_cost BETWEEN 1 AND 9007199254740991",
            name=op.f("ck_purchase_order_lines_line_cost_range"),
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_purchase_order_lines_line_no_positive")),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99999999", name=op.f("ck_purchase_order_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "quantity::numeric * unit_cost = line_cost",
            name=op.f("ck_purchase_order_lines_line_cost_matches"),
        ),
        sa.CheckConstraint(
            "unit_cost BETWEEN 1 AND 9007199254740991",
            name=op.f("ck_purchase_order_lines_unit_cost_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_purchase_order_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["po_id", "currency"],
            ["purchase_orders.id", "purchase_orders.currency"],
            name=op.f("fk_purchase_order_lines_po_id_currency_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            name=op.f("fk_purchase_order_lines_sku_id_skus"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_purchase_order_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_lines")),
    )
    op.create_index(
        "ix_purchase_order_lines_po_id", "purchase_order_lines", ["po_id"], unique=False
    )
    op.create_index(
        "ix_purchase_order_lines_sku_id", "purchase_order_lines", ["sku_id"], unique=False
    )
    op.create_index(
        "uq_purchase_order_lines_po_id_line_no_active",
        "purchase_order_lines",
        ["po_id", "line_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_purchase_order_lines_po_id_sku_id_active",
        "purchase_order_lines",
        ["po_id", "sku_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_purchase_order_lines_po_id_sku_id_active",
        table_name="purchase_order_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_purchase_order_lines_po_id_line_no_active",
        table_name="purchase_order_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_purchase_order_lines_sku_id", table_name="purchase_order_lines")
    op.drop_index("ix_purchase_order_lines_po_id", table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")
    op.drop_index(
        "uq_purchase_order_status_log_purchase_order_id_birth",
        table_name="purchase_order_status_log",
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.drop_index(
        "ix_purchase_order_status_log_purchase_order_id_id", table_name="purchase_order_status_log"
    )
    op.drop_table("purchase_order_status_log")
    op.drop_index(
        "uq_purchase_orders_copied_from_id_live",
        table_name="purchase_orders",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND copied_from_id IS NOT NULL AND status NOT IN ('CANCELLED', 'EXPIRED')"
        ),
    )
    op.drop_index("ix_purchase_orders_supplier_partner_id_doc_date", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_status_doc_date", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_assignee_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")
