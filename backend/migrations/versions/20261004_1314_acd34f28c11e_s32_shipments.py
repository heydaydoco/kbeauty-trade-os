"""s32_shipments

리비전 ID: acd34f28c11e
직전 리비전: 394c76a7d2b7
생성 시각(UTC): 2026-10-04 13:14:44.942982+00:00

S3-2 PR-3a 수출선적 커널 스키마 — M14 (ADR-0074·0077·0078 / design-integrated §2.1 (a)~(d)·§2.12·§9 R-04·R-15·R-16·R-23 / design-A A1~A9):
  ① shipments — 선적 헤더(전표 커널 편입 — DocKind SHIPMENT·접두어 SH, 상태 8값 CHECK[활성 3: PLANNED·RELEASE_ORDERED·CANCELLED,
     RESERVED 5: 피킹~종결], TradeHeaderMixin 전 열 + 구분 4값[shipment_kind]·원천 FK[so_id·po_id]·거래 상대 사본·출발국/도착국(ISO alpha-2,
     markets FK 아님)·합계·동결 시각. CHECK: 헤더 공통 25종 + kind_valid·**kind_source**[원천 FK 정확히 하나 + 수출·수입만 — 채널입고·샘플무상 행
     DB 거부]·import_has_no_amount[PO 원가 비복사]·country_format·counterparty_name_not_blank·source_terms_complete[원천 사본 완결]·
     no_copy_lineage·planned_not_frozen·released_frozen. UNIQUE doc_number(전역)·(id, currency)[라인 복합 FK 대상], 부분 인덱스 so_id·po_id live·목록·담당)
  ② shipment_parties — 당사자(역할 5값 CHECK, (선적, 역할) unique_active, 자동 스냅샷 행은 CONSIGNEE·SHIPPER만 — auto_role, 영문명 비공백·제어문자 금지)
  ③ shipment_status_log — 선적 상태 변경 이력(IMMUTABLE — revoke_mutations, status_log_checks 7종 + 탄생 행 유일)
  ④ shipment_lines — 선적 라인(원천 라인 FK 정확히 하나[one_source], 헤더 복합 FK (shipment_id, currency), 수출 = SO 단가 사본·numeric 곱
     금액[export_priced, R-04]·무상 규약 승계[export_free_iff_zero_price], 수입 = 단가 NULL·금액 0[import_no_price], (선적, 원천 라인) unique_active)

체크리스트:
  ■ 신규 테이블 4개만(기존 표 변경 0 — SO 상태 CHECK에 IN_SHIPMENT가 이미 있고 confirmed_at_consistent가 수용, SO 상태이력 CHECK는
    STATUSES 파생이라 SO 자동 엣지 추가만으로 재생성 불요 — PR-3a 첫 커밋 실측). customs_records는 M15(R-16).
  ■ CHECK는 create_table 안에 op.f() 최종 이름이다(함정 ①·⑪ — alembic check가 CHECK를 못 보므로 정의문 시험
    tests/integration/test_shipment_constraints.py가 pg_get_constraintdef로 고정한다). 식별자 최장 49자(63자 이내).
  ■ 멱등·중복 UNIQUE는 unique_active 부분 인덱스(WHERE deleted_at IS NULL — §17.4), doc_number는 전역 UNIQUE(재발급 금지 §17.3).
  ■ **시드 0** — 전부 ActorMixin(users FK) 소유 표라 마이그레이션 시드 금지(함정 ⑩).
  ■ shipment_status_log는 REVOKE UPDATE, DELETE, TRUNCATE를 손으로 부른다(table_policy.IMMUTABLE_TABLES와 짝).
  ■ downgrade는 인덱스 → 테이블 역순 drop(REVOKE는 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다(운영 실데이터 반입 전 — 계획 §8 ⑩).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations


revision: str = "acd34f28c11e"
down_revision: str | None = "394c76a7d2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_kind", sa.String(length=16), nullable=False),
        sa.Column("so_id", sa.BigInteger(), nullable=True),
        sa.Column("po_id", sa.BigInteger(), nullable=True),
        sa.Column("counterparty_partner_id", sa.BigInteger(), nullable=False),
        sa.Column("counterparty_name", sa.String(length=200), nullable=False),
        sa.Column("origin_country_code", sa.CHAR(length=2), nullable=False),
        sa.Column("dest_country_code", sa.CHAR(length=2), nullable=False),
        sa.Column("total_amount", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("ck_shipments_payment_terms_shape"),
        ),
        sa.CheckConstraint(
            "(shipment_kind = 'EXPORT' AND so_id IS NOT NULL AND po_id IS NULL) OR (shipment_kind = 'IMPORT' AND po_id IS NOT NULL AND so_id IS NULL)",
            name=op.f("ck_shipments_kind_source"),
        ),
        sa.CheckConstraint(
            "balance_anchor IS NULL OR balance_anchor IN ('ARRIVAL_DATE', 'BL_DATE', 'ETD_DATE', 'INVOICE_DATE', 'ORDER_DATE', 'RECEIPT_DATE')",
            name=op.f("ck_shipments_balance_anchor_valid"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days >= 0 OR balance_anchor = 'ETD_DATE'",
            name=op.f("ck_shipments_neg_days_etd_only"),
        ),
        sa.CheckConstraint(
            "btrim(counterparty_name) <> ''", name=op.f("ck_shipments_counterparty_name_not_blank")
        ),
        sa.CheckConstraint(
            "currency <> 'KRW' OR fx_rate IS NULL OR fx_rate = 1",
            name=op.f("ck_shipments_krw_fx_is_one"),
        ),
        sa.CheckConstraint(
            "doc_number ~ '^SH-[0-9]{4}-[0-9]{4,}$'", name=op.f("ck_shipments_doc_number_format")
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DAT' OR incoterm_year = 2010",
            name=op.f("ck_shipments_incoterm_dat_2010"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS DISTINCT FROM 'DPU' OR incoterm_year = 2020",
            name=op.f("ck_shipments_incoterm_dpu_2020"),
        ),
        sa.CheckConstraint(
            "incoterm_code IS NULL OR incoterm_code IN ('CFR', 'CIF', 'CIP', 'CPT', 'DAP', 'DAT', 'DDP', 'DPU', 'EXW', 'FAS', 'FCA', 'FOB')",
            name=op.f("ck_shipments_incoterm_code_valid"),
        ),
        sa.CheckConstraint(
            "incoterm_place IS NULL OR btrim(incoterm_place) <> ''",
            name=op.f("ck_shipments_incoterm_place_not_blank"),
        ),
        sa.CheckConstraint(
            "origin_country_code ~ '^[A-Z]{2}$' AND dest_country_code ~ '^[A-Z]{2}$'",
            name=op.f("ck_shipments_country_format"),
        ),
        sa.CheckConstraint(
            "payment_type IS NULL OR payment_type IN ('LC', 'TT_ADVANCE', 'TT_DEFERRED')",
            name=op.f("ck_shipments_payment_type_valid"),
        ),
        sa.CheckConstraint(
            "shipment_kind <> 'IMPORT' OR total_amount = 0",
            name=op.f("ck_shipments_import_has_no_amount"),
        ),
        sa.CheckConstraint(
            "shipment_kind IN ('CHANNEL_INBOUND', 'EXPORT', 'IMPORT', 'SAMPLE_FREE')",
            name=op.f("ck_shipments_kind_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'PLANNED' OR frozen_at IS NULL", name=op.f("ck_shipments_planned_not_frozen")
        ),
        sa.CheckConstraint(
            "status IN ('CANCELLED', 'CLOSED', 'INSPECTED', 'PICKING', 'PLANNED', 'RELEASED', 'RELEASE_ORDERED', 'SHIPPED')",
            name=op.f("ck_shipments_status_valid"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('CLOSED', 'INSPECTED', 'PICKING', 'RELEASED', 'RELEASE_ORDERED', 'SHIPPED') OR frozen_at IS NOT NULL",
            name=op.f("ck_shipments_released_frozen"),
        ),
        sa.CheckConstraint(
            "(fx_rate IS NULL) = (fx_rate_date IS NULL)", name=op.f("ck_shipments_fx_pair")
        ),
        sa.CheckConstraint(
            "(incoterm_code IS NULL AND incoterm_place IS NULL AND incoterm_year IS NULL) OR (incoterm_code IS NOT NULL AND incoterm_place IS NOT NULL AND incoterm_year IS NOT NULL)",
            name=op.f("ck_shipments_incoterm_all_or_none"),
        ),
        sa.CheckConstraint(
            "balance_days IS NULL OR balance_days BETWEEN -90 AND 365",
            name=op.f("ck_shipments_balance_days_range"),
        ),
        sa.CheckConstraint(
            "copied_from_id IS NULL OR copied_from_id <> id",
            name=op.f("ck_shipments_copied_from_not_self"),
        ),
        sa.CheckConstraint("copied_from_id IS NULL", name=op.f("ck_shipments_no_copy_lineage")),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_shipments_currency_uppercase")
        ),
        sa.CheckConstraint(
            "fx_rate > 0 AND fx_rate <= 1000000", name=op.f("ck_shipments_fx_rate_range")
        ),
        sa.CheckConstraint(
            "fx_rate_date IS NULL OR fx_rate_date <= doc_date",
            name=op.f("ck_shipments_fx_date_not_future"),
        ),
        sa.CheckConstraint(
            "incoterm_year IS NULL OR incoterm_year IN (2010, 2020)",
            name=op.f("ck_shipments_incoterm_year_valid"),
        ),
        sa.CheckConstraint("last_line_no >= 0", name=op.f("ck_shipments_last_line_no_nonnegative")),
        sa.CheckConstraint(
            "payment_type IS NOT NULL AND incoterm_code IS NOT NULL AND fx_rate IS NOT NULL",
            name=op.f("ck_shipments_source_terms_complete"),
        ),
        sa.CheckConstraint(
            "total_amount BETWEEN 0 AND 9007199254740991", name=op.f("ck_shipments_total_range")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_shipments_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_id"],
            ["shipments.id"],
            name=op.f("fk_shipments_copied_from_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["counterparty_partner_id"],
            ["partners.id"],
            name=op.f("fk_shipments_counterparty_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_shipments_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["po_id"],
            ["purchase_orders.id"],
            name=op.f("fk_shipments_po_id_purchase_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["so_id"],
            ["sales_orders.id"],
            name=op.f("fk_shipments_so_id_sales_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_shipments_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipments")),
        sa.UniqueConstraint("doc_number", name="uq_shipments_doc_number"),
        sa.UniqueConstraint("id", "currency", name="uq_shipments_id_currency"),
    )
    op.create_index("ix_shipments_assignee_id", "shipments", ["assignee_id"], unique=False)
    op.create_index(
        "ix_shipments_counterparty_partner_id",
        "shipments",
        ["counterparty_partner_id"],
        unique=False,
    )
    op.create_index(
        "ix_shipments_list",
        "shipments",
        ["status", sa.literal_column("id DESC")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_shipments_po_id_live",
        "shipments",
        ["po_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND po_id IS NOT NULL"),
    )
    op.create_index(
        "ix_shipments_so_id_live",
        "shipments",
        ["so_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL AND so_id IS NOT NULL"),
    )
    op.create_table(
        "shipment_parties",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("name_en", sa.String(length=200), nullable=False),
        sa.Column("address_en", sa.String(length=500), nullable=True),
        sa.Column("is_auto", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            "NOT is_auto OR role IN ('CONSIGNEE', 'SHIPPER')",
            name=op.f("ck_shipment_parties_auto_role"),
        ),
        sa.CheckConstraint(
            "address_en IS NULL OR btrim(address_en) <> ''",
            name=op.f("ck_shipment_parties_address_en_not_blank"),
        ),
        sa.CheckConstraint(
            "btrim(name_en) <> '' AND name_en !~ '[[:cntrl:]]'",
            name=op.f("ck_shipment_parties_name_en_clean"),
        ),
        sa.CheckConstraint(
            "role IN ('CONSIGNEE', 'CUSTOMS_BROKER', 'FORWARDER', 'NOTIFY', 'SHIPPER')",
            name=op.f("ck_shipment_parties_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_shipment_parties_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.id"],
            name=op.f("fk_shipment_parties_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_shipment_parties_shipment_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_shipment_parties_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_parties")),
    )
    op.create_index(
        "ix_shipment_parties_partner_id", "shipment_parties", ["partner_id"], unique=False
    )
    op.create_index(
        "ix_shipment_parties_shipment_id", "shipment_parties", ["shipment_id"], unique=False
    )
    op.create_index(
        "uq_shipment_parties_shipment_id_role_active",
        "shipment_parties",
        ["shipment_id", "role"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "shipment_status_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
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
            "from_status IS NOT NULL OR to_status = 'PLANNED'",
            name=op.f("ck_shipment_status_log_birth_row"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('CANCELLED', 'CLOSED', 'INSPECTED', 'PICKING', 'PLANNED', 'RELEASED', 'RELEASE_ORDERED', 'SHIPPED')",
            name=op.f("ck_shipment_status_log_from_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status IN ('CANCELLED', 'CLOSED', 'INSPECTED', 'PICKING', 'PLANNED', 'RELEASED', 'RELEASE_ORDERED', 'SHIPPED')",
            name=op.f("ck_shipment_status_log_to_status_valid"),
        ),
        sa.CheckConstraint(
            "to_status NOT IN ('CANCELLED') OR reason IS NOT NULL",
            name=op.f("ck_shipment_status_log_reason_required"),
        ),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL OR automatic",
            name=op.f("ck_shipment_status_log_actor_or_automatic"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name=op.f("ck_shipment_status_log_no_self_transition"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500",
            name=op.f("ck_shipment_status_log_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_shipment_status_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"],
            ["shipments.id"],
            name=op.f("fk_shipment_status_log_shipment_id_shipments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_status_log")),
    )
    op.create_index(
        "ix_shipment_status_log_shipment_id_id",
        "shipment_status_log",
        ["shipment_id", sa.literal_column("id DESC")],
        unique=False,
    )
    revoke_mutations(op, "shipment_status_log")
    op.create_index(
        "uq_shipment_status_log_shipment_id_birth",
        "shipment_status_log",
        ["shipment_id"],
        unique=True,
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.create_table(
        "shipment_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("so_line_id", sa.BigInteger(), nullable=True),
        sa.Column("po_line_id", sa.BigInteger(), nullable=True),
        sa.Column("sku_id", sa.BigInteger(), nullable=False),
        sa.Column("sku_code", sa.String(length=40), nullable=False),
        sa.Column("sku_name_ko", sa.String(length=200), nullable=False),
        sa.Column("sku_name_en", sa.String(length=200), nullable=True),
        sa.Column("sku_kind", sa.String(length=6), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_amount", sa.BigInteger(), nullable=True),
        sa.Column("is_free", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("line_amount", sa.BigInteger(), nullable=False),
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
            "sku_kind IN ('SET', 'SINGLE')", name=op.f("ck_shipment_lines_sku_kind_valid")
        ),
        sa.CheckConstraint(
            "(so_line_id IS NULL) <> (po_line_id IS NULL)",
            name=op.f("ck_shipment_lines_one_source"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_shipment_lines_currency_uppercase")
        ),
        sa.CheckConstraint(
            "line_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_shipment_lines_amount_range"),
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_shipment_lines_line_no_positive")),
        sa.CheckConstraint(
            "po_line_id IS NULL OR (unit_price_amount IS NULL AND line_amount = 0 AND NOT is_free)",
            name=op.f("ck_shipment_lines_import_no_price"),
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99999999", name=op.f("ck_shipment_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "so_line_id IS NULL OR (unit_price_amount IS NOT NULL AND quantity::numeric * unit_price_amount = line_amount)",
            name=op.f("ck_shipment_lines_export_priced"),
        ),
        sa.CheckConstraint(
            "so_line_id IS NULL OR is_free = (unit_price_amount = 0)",
            name=op.f("ck_shipment_lines_export_free_iff_zero_price"),
        ),
        sa.CheckConstraint(
            "unit_price_amount IS NULL OR unit_price_amount BETWEEN 0 AND 9007199254740991",
            name=op.f("ck_shipment_lines_unit_price_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_shipment_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["po_line_id"],
            ["purchase_order_lines.id"],
            name=op.f("fk_shipment_lines_po_line_id_purchase_order_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id", "currency"],
            ["shipments.id", "shipments.currency"],
            name=op.f("fk_shipment_lines_shipment_id_currency_shipments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name=op.f("fk_shipment_lines_sku_id_skus"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["so_line_id"],
            ["sales_order_lines.id"],
            name=op.f("fk_shipment_lines_so_line_id_sales_order_lines"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_shipment_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shipment_lines")),
    )
    op.create_index("ix_shipment_lines_po_line_id", "shipment_lines", ["po_line_id"], unique=False)
    op.create_index(
        "ix_shipment_lines_shipment_id", "shipment_lines", ["shipment_id"], unique=False
    )
    op.create_index("ix_shipment_lines_sku_id", "shipment_lines", ["sku_id"], unique=False)
    op.create_index("ix_shipment_lines_so_line_id", "shipment_lines", ["so_line_id"], unique=False)
    op.create_index(
        "uq_shipment_lines_shipment_id_line_no_active",
        "shipment_lines",
        ["shipment_id", "line_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_shipment_lines_shipment_id_po_line_id_active",
        "shipment_lines",
        ["shipment_id", "po_line_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_shipment_lines_shipment_id_so_line_id_active",
        "shipment_lines",
        ["shipment_id", "so_line_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_shipment_lines_shipment_id_so_line_id_active",
        table_name="shipment_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_shipment_lines_shipment_id_po_line_id_active",
        table_name="shipment_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_shipment_lines_shipment_id_line_no_active",
        table_name="shipment_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_shipment_lines_so_line_id", table_name="shipment_lines")
    op.drop_index("ix_shipment_lines_sku_id", table_name="shipment_lines")
    op.drop_index("ix_shipment_lines_shipment_id", table_name="shipment_lines")
    op.drop_index("ix_shipment_lines_po_line_id", table_name="shipment_lines")
    op.drop_table("shipment_lines")
    op.drop_index(
        "uq_shipment_status_log_shipment_id_birth",
        table_name="shipment_status_log",
        postgresql_where=sa.text("from_status IS NULL"),
    )
    op.drop_index("ix_shipment_status_log_shipment_id_id", table_name="shipment_status_log")
    op.drop_table("shipment_status_log")
    op.drop_index(
        "uq_shipment_parties_shipment_id_role_active",
        table_name="shipment_parties",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_shipment_parties_shipment_id", table_name="shipment_parties")
    op.drop_index("ix_shipment_parties_partner_id", table_name="shipment_parties")
    op.drop_table("shipment_parties")
    op.drop_index(
        "ix_shipments_so_id_live",
        table_name="shipments",
        postgresql_where=sa.text("deleted_at IS NULL AND so_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_shipments_po_id_live",
        table_name="shipments",
        postgresql_where=sa.text("deleted_at IS NULL AND po_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_shipments_list", table_name="shipments", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_index("ix_shipments_counterparty_partner_id", table_name="shipments")
    op.drop_index("ix_shipments_assignee_id", table_name="shipments")
    op.drop_table("shipments")
