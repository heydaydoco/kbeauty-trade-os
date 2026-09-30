"""s31_policy_settings

리비전 ID: 0e42f85591de
직전 리비전: a1f4c7e2b9d3
생성 시각(UTC): 2026-09-30 04:38:18.741380+00:00

S3-1 PR-4 정책 저장소 (ADR-0065 — 설계 design-E E8):
  policy_settings — 정책 키 하나당 현재 값 한 행. 키 집합은 폐쇄 CHECK(policy_key_closed)이고
  코드 레지스트리(app/modules/policies/registry.py)와 1:1이다(아키텍처 테스트가 대사).
  값 형 CHECK(policy_value_shape)는 키별 값 범위·형 교차를 거부한다.

체크리스트:
  ■ 신규 테이블만 — CHECK 2종은 create_table 안에 있어 autogenerate가 감지한 것을 확인했고
    이름은 op.f()로 이미 최종 이름이다(함정 ①·⑪). 부분 유니크 인덱스는 unique_active()다(§17.4).
  ■ **시드 0** — 행이 없는 것이 정상 초기 상태이고 그때 동작은 레지스트리의 unset_value(fail-closed)다
    (함정 ⑩: ActorMixin users FK 테이블은 마이그레이션 시드 금지).
  ■ downgrade는 인덱스 → 테이블 순으로 되돌린다(데이터가 있으면 함께 사라진다 — 변경 이력은 audit_log).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0e42f85591de"
down_revision: str | None = "a1f4c7e2b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "policy_settings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("policy_key", sa.String(length=60), nullable=False),
        sa.Column("value_text", sa.String(length=20), nullable=True),
        sa.Column("value_int", sa.Integer(), nullable=True),
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
            "(policy_key = 'pi_advance_gate_mode' AND value_int IS NULL AND value_text IN ('OFF', 'WARN', 'BLOCK')) OR (policy_key = 'price_deviation_tolerance_bp' AND value_text IS NULL AND value_int BETWEEN 0 AND 10000)",
            name=op.f("ck_policy_settings_policy_value_shape"),
        ),
        sa.CheckConstraint(
            "policy_key IN ('pi_advance_gate_mode', 'price_deviation_tolerance_bp')",
            name=op.f("ck_policy_settings_policy_key_closed"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_policy_settings_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_policy_settings_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_settings")),
    )
    op.create_index(
        "uq_policy_settings_policy_key_active",
        "policy_settings",
        ["policy_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_policy_settings_policy_key_active",
        table_name="policy_settings",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("policy_settings")
