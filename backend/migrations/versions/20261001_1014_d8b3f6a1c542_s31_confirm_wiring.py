"""s31_confirm_wiring

리비전 ID: d8b3f6a1c542
직전 리비전: c5f1a9d27b30
생성 시각(UTC): 2026-10-01 10:14:43.563177+00:00

S3-1 PR-12a 확정 배선 — M10 ALTER (ADR-0070 / design-E E4 / design-integrated X-11·X-49 · §2.1):
  `sales_orders` + 확정 증적 3열(`credit_verdict`·`credit_approval_id`·`pi_gate_verdict`) — SO 행 자체가 "게이트를 통과했다"는 사실을 증명하고 DB CHECK가 게이트 없는 확정을 거부한다.
    CHECK 5종: 값 집합 2 + `(confirmed_at IS NULL) = (credit_verdict IS NULL)`·`(confirmed_at IS NULL) = (pi_gate_verdict IS NULL)`
    + `((credit_verdict = 'APPROVED') IS TRUE) = (credit_approval_id IS NOT NULL)`(NULL이 비교를 통과하는 함정을 `IS TRUE`로 회피).
    `credit_approval_id` FK approvals RESTRICT + 부분 유니크 `uq_sales_orders_credit_approval_id`(1승인=1SO) + 여신 노출 조회 축 `ix_sales_orders_open_exposure`.
  `sales_order_status_log` + `approval_id`(FK approvals RESTRICT) — 확정 행이 소비한 승인 참조. CHECK `approval_only_on_confirm`(RECEIVED→CONFIRMED 행에만)·부분 유니크(1승인=1이력행).
  (통합 X-11: 상세 증적은 `gate_evaluations` CONFIRMED 행 하나가 원천이라 SO에는 3열만 둔다 — `pi_gate_override_reason`·`confirm_gate_snapshot`은 만들지 않는다.)

체크리스트:
  ■ additive만 — 기존 열·제약을 바꾸지 않는다. **시드 0**. 표가 비어 있어 백필이 없다(확정 SO가 이미 있으면 CHECK가 실패하므로 사전 검사가 이유를 알려 주고 중단한다 —
    확정 통로가 없던 PR-7a~PR-11 동안 정상 경로로는 확정 SO가 생길 수 없다).
  ■ **CHECK는 autogenerate가 못 보므로 손으로 부른다**(op.create_check_constraint + op.f() 최종 이름). 정의문 테스트(tests/integration/test_sales_order_constraints.py)가 고정한다.
  ■ 제약·인덱스 이름 63자 이내(최장 `ck_sales_orders_credit_approval_iff_approved` 43자·`uq_sales_order_status_log_approval_id` 36자).
  ■ downgrade는 인덱스 → 제약 → FK → 열 역순 drop(데이터는 증적 열이라 함께 사라진다 — 운영 데이터가 생긴 뒤에는 되돌리지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8b3f6a1c542"
down_revision: str | None = "c5f1a9d27b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREDIT_VERDICTS = "'WITHIN_LIMIT', 'NOT_MANAGED', 'APPROVED'"
_PI_GATE_VERDICTS = "'PASS', 'NOT_APPLICABLE', 'WARN', 'OVERRIDDEN', 'SKIPPED_OFF'"
_OPEN_EXPOSURE = (
    "confirmed_at IS NOT NULL AND deleted_at IS NULL AND status NOT IN ('COMPLETED', 'CANCELLED')"
)

_SO_CHECKS: tuple[tuple[str, str], ...] = (
    ("credit_verdict_valid", f"credit_verdict IS NULL OR credit_verdict IN ({_CREDIT_VERDICTS})"),
    (
        "pi_gate_verdict_valid",
        f"pi_gate_verdict IS NULL OR pi_gate_verdict IN ({_PI_GATE_VERDICTS})",
    ),
    ("credit_verdict_iff_confirmed", "(confirmed_at IS NULL) = (credit_verdict IS NULL)"),
    ("pi_gate_verdict_iff_confirmed", "(confirmed_at IS NULL) = (pi_gate_verdict IS NULL)"),
    (
        "credit_approval_iff_approved",
        "((credit_verdict = 'APPROVED') IS TRUE) = (credit_approval_id IS NOT NULL)",
    ),
)


def upgrade() -> None:
    existing = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM sales_orders WHERE confirmed_at IS NOT NULL"))
        .scalar_one()
    )
    if existing:
        raise RuntimeError(
            f"확정된 수주가 {existing}건 있어 확정 증적 CHECK를 추가할 수 없습니다 — 게이트 통과 기록이 없는 "
            "확정은 백필하지 않습니다(설계상 이 마이그레이션 시점의 SO 표에는 확정 행이 없어야 합니다)."
        )

    op.add_column(
        "sales_order_status_log", sa.Column("approval_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_sales_order_status_log_approval_id_approvals"),
        "sales_order_status_log",
        "approvals",
        ["approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_sales_order_status_log_approval_only_on_confirm"),
        "sales_order_status_log",
        "approval_id IS NULL OR (from_status = 'RECEIVED' AND to_status = 'CONFIRMED')",
    )
    op.create_index(
        "uq_sales_order_status_log_approval_id",
        "sales_order_status_log",
        ["approval_id"],
        unique=True,
        postgresql_where=sa.text("approval_id IS NOT NULL"),
    )

    op.add_column("sales_orders", sa.Column("credit_verdict", sa.String(length=12), nullable=True))
    op.add_column("sales_orders", sa.Column("credit_approval_id", sa.BigInteger(), nullable=True))
    op.add_column("sales_orders", sa.Column("pi_gate_verdict", sa.String(length=16), nullable=True))
    op.create_foreign_key(
        op.f("fk_sales_orders_credit_approval_id_approvals"),
        "sales_orders",
        "approvals",
        ["credit_approval_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for name, condition in _SO_CHECKS:
        op.create_check_constraint(op.f(f"ck_sales_orders_{name}"), "sales_orders", condition)
    op.create_index(
        "uq_sales_orders_credit_approval_id",
        "sales_orders",
        ["credit_approval_id"],
        unique=True,
        postgresql_where=sa.text("credit_approval_id IS NOT NULL"),
    )
    op.create_index(
        "ix_sales_orders_open_exposure",
        "sales_orders",
        ["buyer_partner_id"],
        unique=False,
        postgresql_where=sa.text(_OPEN_EXPOSURE),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_orders_open_exposure",
        table_name="sales_orders",
        postgresql_where=sa.text(_OPEN_EXPOSURE),
    )
    op.drop_index(
        "uq_sales_orders_credit_approval_id",
        table_name="sales_orders",
        postgresql_where=sa.text("credit_approval_id IS NOT NULL"),
    )
    for name, _ in reversed(_SO_CHECKS):
        op.drop_constraint(op.f(f"ck_sales_orders_{name}"), "sales_orders", type_="check")
    op.drop_constraint(
        op.f("fk_sales_orders_credit_approval_id_approvals"), "sales_orders", type_="foreignkey"
    )
    op.drop_column("sales_orders", "pi_gate_verdict")
    op.drop_column("sales_orders", "credit_approval_id")
    op.drop_column("sales_orders", "credit_verdict")

    op.drop_index(
        "uq_sales_order_status_log_approval_id",
        table_name="sales_order_status_log",
        postgresql_where=sa.text("approval_id IS NOT NULL"),
    )
    op.drop_constraint(
        op.f("ck_sales_order_status_log_approval_only_on_confirm"),
        "sales_order_status_log",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_sales_order_status_log_approval_id_approvals"),
        "sales_order_status_log",
        type_="foreignkey",
    )
    op.drop_column("sales_order_status_log", "approval_id")
