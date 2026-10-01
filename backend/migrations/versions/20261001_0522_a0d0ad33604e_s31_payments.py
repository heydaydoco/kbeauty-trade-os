"""s31_payments

리비전 ID: a0d0ad33604e
직전 리비전: e312f01426d4
생성 시각(UTC): 2026-10-01 05:22:19.677821+00:00

S3-1 PR-10a 입금 원장 — M08 (ADR-0068 / design-E E7 / design-integrated §2.1):
  `payments` — 부호 있는 INSERT-only 원장(PkMixin만 — Version·SoftDelete·Actor·Timestamp 믹스인 없음). RECEIPT > 0 / REVERSAL < 0(원 입금의 −전액).
  역기록은 같은 PI·같은 통화의 원 입금만 가리킨다(복합 FK `(reverses_payment_id, pi_id, received_currency)` → `UNIQUE(id, pi_id, received_currency)`)
  이고 한 입금은 한 번만 역기록된다(부분 유니크 `uq_payments_reverses_payment_id`). CHECK `kind_sign`·참조/사유 제어문자 거부(`reference_clean`·`reason_clean`)·통화 `^[A-Z]{3}$`·참조 비공백(탭·개행 포함)이 부호·역기록 필수 열·사유(≥2자)를 묶고
  REVERSAL이면 `reverses_payment_id NOT NULL`을 요구해 복합 FK의 MATCH SIMPLE 공백을 메운다.

체크리스트:
  ■ 신규 테이블 1개만(백필·기존 테이블 변경 없음). **시드 0** — 입금은 업무 행위의 기록이라 마이그레이션 시드 금지(함정 ⑩, _NEVER_SEEDED 등재).
  ■ CHECK는 create_table 안 op.f() 최종 이름이다(alembic check가 CHECK를 못 보므로 정의문 테스트 tests/integration/test_payment_constraints.py가 고정).
  ■ **GRANT/REVOKE는 autogenerate가 못 보므로 손으로 부른다**: `revoke_mutations(op, "payments")` — 앱 계정(kbos_app)은 INSERT·SELECT만(UPDATE·DELETE·TRUNCATE 42501).
  ■ 제약·인덱스 이름 63자 이내(최장 `uq_payments_id_pi_id_received_currency` 38자·`fk_payments_reverses_same_pi_currency` 37자 — 실측 테스트가 고정).
  ■ downgrade는 인덱스 → 테이블 역순 drop(권한은 테이블과 함께 소멸). 데이터가 있으면 함께 사라진다(입금 원장 — 운영 데이터가 생긴 뒤에는 되돌리지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.db.table_policy import revoke_mutations

revision: str = "a0d0ad33604e"
down_revision: str | None = "e312f01426d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("pi_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("received_amount", sa.BigInteger(), nullable=False),
        sa.Column("received_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("reverses_payment_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("recorded_by_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'RECEIPT' AND received_amount > 0 AND reverses_payment_id IS NULL AND reason IS NULL) OR (kind = 'REVERSAL' AND received_amount < 0 AND reverses_payment_id IS NOT NULL AND reason IS NOT NULL AND char_length(btrim(reason)) >= 2)",
            name=op.f("ck_payments_kind_sign"),
        ),
        sa.CheckConstraint(
            "btrim(reference, E' \\t\\r\\n') <> ''",
            name=op.f("ck_payments_reference_not_blank"),
        ),
        sa.CheckConstraint("reference !~ '[[:cntrl:]]'", name=op.f("ck_payments_reference_clean")),
        sa.CheckConstraint(
            "reason IS NULL OR reason !~ '[[:cntrl:]]'", name=op.f("ck_payments_reason_clean")
        ),
        sa.CheckConstraint("kind IN ('RECEIPT', 'REVERSAL')", name=op.f("ck_payments_kind_valid")),
        sa.CheckConstraint(
            "abs(received_amount) <= 9007199254740991",
            name=op.f("ck_payments_received_amount_range"),
        ),
        sa.CheckConstraint(
            "received_currency ~ '^[A-Z]{3}$'", name=op.f("ck_payments_currency_upper")
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.id"],
            name=op.f("fk_payments_partner_id_partners"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pi_id"],
            ["proforma_invoices.id"],
            name=op.f("fk_payments_pi_id_proforma_invoices"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_id"],
            ["users.id"],
            name=op.f("fk_payments_recorded_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_payment_id", "pi_id", "received_currency"],
            ["payments.id", "payments.pi_id", "payments.received_currency"],
            name="fk_payments_reverses_same_pi_currency",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint(
            "id", "pi_id", "received_currency", name="uq_payments_id_pi_id_received_currency"
        ),
    )
    op.create_index("ix_payments_partner_id", "payments", ["partner_id"], unique=False)
    op.create_index("ix_payments_pi", "payments", ["pi_id", "id"], unique=False)
    op.create_index("ix_payments_recorded_by_id", "payments", ["recorded_by_id"], unique=False)
    op.create_index(
        "uq_payments_reverses_payment_id",
        "payments",
        ["reverses_payment_id"],
        unique=True,
        postgresql_where=sa.text("reverses_payment_id IS NOT NULL"),
    )
    revoke_mutations(op, "payments")


def downgrade() -> None:
    op.drop_index(
        "uq_payments_reverses_payment_id",
        table_name="payments",
        postgresql_where=sa.text("reverses_payment_id IS NOT NULL"),
    )
    op.drop_index("ix_payments_recorded_by_id", table_name="payments")
    op.drop_index("ix_payments_pi", table_name="payments")
    op.drop_index("ix_payments_partner_id", table_name="payments")
    op.drop_table("payments")
