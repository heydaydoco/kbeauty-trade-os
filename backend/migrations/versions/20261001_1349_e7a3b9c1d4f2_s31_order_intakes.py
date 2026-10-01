"""s31_order_intakes

리비전 ID: e7a3b9c1d4f2
직전 리비전: d8b3f6a1c542
생성 시각(UTC): 2026-10-01 13:49:28+00:00

S3-1 PR-13a 오더 인테이크 — M11 (ADR-0071 / design-D D1·D2 / design-integrated §2.1 (f) · X-13):
  ① order_intakes      — 바이어 PO 스테이징 헤더. 상태 3값(PENDING·CONFIRMED·REJECTED)·`extracted_snapshot` 불변 원본·거래처/통화 불변·
     **중복 바이어 PO 0건 = 부분 유니크 uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending**(PENDING 한정 — CONFIRMED는 SO 유니크가, REJECTED는 키 해방)·
     PENDING 파일 해시 유니크(CSV 입구 PR-14용)·SO 백링크 유니크(인테이크 1건=SO 1건)·**copied_from_so_id**(X-13 — 같은 원본 SO를 복제하는 PENDING은 하나, 자율 확정)·
     결정 일관성 CHECK(PENDING ⇔ 결정 시각·결정자 없음, CONFIRMED ⇔ sales_order_id, REJECTED ⇔ 사유 5~500자·제어문자 불가)·UNIQUE(id, currency)=라인 복합 FK 대상.
  ② order_intake_lines — 라인(바이어 품번·수량·단가≥1·요청납기·SKU 해석 저장본). (intake_id, currency) 복합 FK로 라인 통화=헤더 통화를 DB가 강제.

체크리스트:
  ■ 신규 테이블 2개만(백필·기존 테이블 변경 없음). **시드 0** — 전부 ActorMixin(users FK) 소유 표라 마이그레이션 시드 금지(함정 ⑩, `_NEVER_SEEDED` 등재).
  ■ CHECK는 create_table 안 op.f() 최종 이름이다(alembic check가 CHECK를 못 보므로 정의문 테스트 tests/integration/test_order_intake_constraints.py가 고정한다 — 함정 ①·⑪).
  ■ 멱등 UNIQUE는 전부 `WHERE deleted_at IS NULL` 부분 인덱스다(§17.4). 제약·인덱스 이름 63자 이내(최장 57자).
  ■ MUTABLE 표(편집·전이·이관이 앱 계정의 정상 UPDATE) — REVOKE 없음. 불변 열은 ORM 가드+AST 스캔+CHECK(트리거 미채택, ADR-0028·0040).
  ■ downgrade는 인덱스 → 테이블 역순 drop. 데이터가 있으면 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a3b9c1d4f2"
down_revision: str | None = "d8b3f6a1c542"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_intakes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("source_kind", sa.String(length=10), nullable=False),
        sa.Column("source_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("source_group_key", sa.String(length=100), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("extracted_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("buyer_partner_id", sa.BigInteger(), nullable=False),
        sa.Column("buyer_po_no", sa.String(length=60), nullable=False),
        sa.Column("buyer_po_no_key", sa.String(length=60), nullable=False),
        sa.Column("buyer_po_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("dest_market_code", sa.String(length=2), nullable=False),
        sa.Column(
            "status", sa.String(length=9), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("assignee_id", sa.BigInteger(), nullable=False),
        sa.Column("last_line_no", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("sales_order_id", sa.BigInteger(), nullable=True),
        sa.Column("copied_from_so_id", sa.BigInteger(), nullable=True),
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
            "((source_kind = 'CSV') = (source_sha256 IS NOT NULL)) AND ((source_sha256 IS NULL) = (source_group_key IS NULL))",
            name=op.f("ck_order_intakes_source_pair"),
        ),
        sa.CheckConstraint(
            "((status = 'PENDING') = (decided_at IS NULL)) AND ((decided_at IS NULL) = (decided_by_id IS NULL))",
            name=op.f("ck_order_intakes_decision_consistent"),
        ),
        sa.CheckConstraint(
            "(status = 'CONFIRMED') = (sales_order_id IS NOT NULL)",
            name=op.f("ck_order_intakes_confirmed_has_so"),
        ),
        sa.CheckConstraint(
            "(status = 'REJECTED') = (reject_reason IS NOT NULL)",
            name=op.f("ck_order_intakes_rejected_has_reason"),
        ),
        sa.CheckConstraint(
            "btrim(buyer_po_no) <> '' AND btrim(buyer_po_no_key) <> ''",
            name=op.f("ck_order_intakes_po_key_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(extracted_snapshot) = 'object'",
            name=op.f("ck_order_intakes_extracted_snapshot_is_object"),
        ),
        sa.CheckConstraint(
            "reject_reason IS NULL OR reject_reason !~ '[[:cntrl:]]'",
            name=op.f("ck_order_intakes_reject_reason_clean"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('CSV', 'MANUAL')", name=op.f("ck_order_intakes_source_kind_valid")
        ),
        sa.CheckConstraint(
            "source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_order_intakes_source_sha256_format"),
        ),
        sa.CheckConstraint(
            "status IN ('CONFIRMED', 'PENDING', 'REJECTED')",
            name=op.f("ck_order_intakes_status_valid"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_order_intakes_currency_uppercase")
        ),
        sa.CheckConstraint(
            "last_line_no >= 0", name=op.f("ck_order_intakes_last_line_no_nonnegative")
        ),
        sa.CheckConstraint(
            "reject_reason IS NULL OR char_length(btrim(reject_reason)) BETWEEN 5 AND 500",
            name=op.f("ck_order_intakes_reject_reason_length"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_order_intakes_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_partner_id"],
            ["partners.id"],
            name=op.f("fk_order_intakes_buyer_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["copied_from_so_id"],
            ["sales_orders.id"],
            name=op.f("fk_order_intakes_copied_from_so_id_sales_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_order_intakes_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_order_intakes_decided_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dest_market_code"],
            ["markets.code"],
            name=op.f("fk_order_intakes_dest_market_code_markets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sales_order_id"],
            ["sales_orders.id"],
            name=op.f("fk_order_intakes_sales_order_id_sales_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_order_intakes_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_intakes")),
        sa.UniqueConstraint("id", "currency", name="uq_order_intakes_id_currency"),
    )
    op.create_index(
        "ix_order_intakes_buyer_partner_id", "order_intakes", ["buyer_partner_id"], unique=False
    )
    op.create_index("ix_order_intakes_created_at", "order_intakes", ["created_at"], unique=False)
    op.create_index(
        "ix_order_intakes_status_assignee_id",
        "order_intakes",
        ["status", "assignee_id"],
        unique=False,
    )
    op.create_index(
        "uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending",
        "order_intakes",
        ["buyer_partner_id", "buyer_po_no_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'PENDING'"),
    )
    op.create_index(
        "uq_order_intakes_copied_from_so_id_pending",
        "order_intakes",
        ["copied_from_so_id"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND status = 'PENDING' AND copied_from_so_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_order_intakes_sales_order_id_active",
        "order_intakes",
        ["sales_order_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND sales_order_id IS NOT NULL"),
    )
    op.create_index(
        "uq_order_intakes_source_sha256_source_group_key_pending",
        "order_intakes",
        ["source_sha256", "source_group_key"],
        unique=True,
        postgresql_where=sa.text(
            "deleted_at IS NULL AND status = 'PENDING' AND source_sha256 IS NOT NULL"
        ),
    )
    op.create_table(
        "order_intake_lines",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("intake_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("buyer_item_code", sa.String(length=100), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_amount", sa.BigInteger(), nullable=False),
        sa.Column("requested_delivery_date", sa.Date(), nullable=True),
        sa.Column("source_row_no", sa.Integer(), nullable=True),
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
            "btrim(buyer_item_code) <> ''",
            name=op.f("ck_order_intake_lines_buyer_item_code_nonblank"),
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f("ck_order_intake_lines_currency_uppercase")
        ),
        sa.CheckConstraint("line_no >= 1", name=op.f("ck_order_intake_lines_line_no_positive")),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 99999999", name=op.f("ck_order_intake_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "source_row_no IS NULL OR source_row_no >= 2",
            name=op.f("ck_order_intake_lines_source_row_no_min"),
        ),
        sa.CheckConstraint(
            "unit_price_amount BETWEEN 1 AND 9007199254740991",
            name=op.f("ck_order_intake_lines_unit_price_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_order_intake_lines_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["intake_id", "currency"],
            ["order_intakes.id", "order_intakes.currency"],
            name=op.f("fk_order_intake_lines_intake_id_currency_order_intakes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            name=op.f("fk_order_intake_lines_sku_id_skus"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_order_intake_lines_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_intake_lines")),
    )
    op.create_index(
        "ix_order_intake_lines_intake_id", "order_intake_lines", ["intake_id"], unique=False
    )
    op.create_index("ix_order_intake_lines_sku_id", "order_intake_lines", ["sku_id"], unique=False)
    op.create_index(
        "uq_order_intake_lines_intake_id_line_no_active",
        "order_intake_lines",
        ["intake_id", "line_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_order_intake_lines_intake_id_line_no_active",
        table_name="order_intake_lines",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_order_intake_lines_sku_id", table_name="order_intake_lines")
    op.drop_index("ix_order_intake_lines_intake_id", table_name="order_intake_lines")
    op.drop_table("order_intake_lines")
    op.drop_index(
        "uq_order_intakes_source_sha256_source_group_key_pending",
        table_name="order_intakes",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND status = 'PENDING' AND source_sha256 IS NOT NULL"
        ),
    )
    op.drop_index(
        "uq_order_intakes_sales_order_id_active",
        table_name="order_intakes",
        postgresql_where=sa.text("deleted_at IS NULL AND sales_order_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_order_intakes_copied_from_so_id_pending",
        table_name="order_intakes",
        postgresql_where=sa.text(
            "deleted_at IS NULL AND status = 'PENDING' AND copied_from_so_id IS NOT NULL"
        ),
    )
    op.drop_index(
        "uq_order_intakes_buyer_partner_id_buyer_po_no_key_pending",
        table_name="order_intakes",
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'PENDING'"),
    )
    op.drop_index("ix_order_intakes_status_assignee_id", table_name="order_intakes")
    op.drop_index("ix_order_intakes_created_at", table_name="order_intakes")
    op.drop_index("ix_order_intakes_buyer_partner_id", table_name="order_intakes")
    op.drop_table("order_intakes")
