"""s31_board_saved_filters

리비전 ID: f2cb6020b2bb
직전 리비전: e7a3b9c1d4f2
생성 시각(UTC): 2026-10-03 06:33:14+00:00

S3-1 PR-15a 오더 보드 저장 필터 — M12 (ADR-0066 / design-D D6 / design-integrated §2.1 (f)·§2.11):
  board_saved_filters — 사용자 1명의 이름 붙은 보드 필터. `user_id`(FK users RESTRICT — `owner_user_id` 등 handover 감지 이름 금지)·
  `name` VARCHAR(60)(비공백·제어문자 불가)·`filter_config` JSONB(객체만 — 내용은 앱의 BoardFilter[extra=forbid]가 저장·읽기 시 재검증)·
  **활성 이름 유일 = 부분 유니크 uq_board_saved_filters_user_id_name_active**(soft delete가 이름을 해방). 사용자당 활성 20개 상한은 서비스가 강제한다.

체크리스트:
  ■ 신규 테이블 1개만(백필·기존 테이블 변경 없음). **시드 0** — ActorMixin(users FK) 소유 표라 마이그레이션 시드 금지(함정 ⑩, `_NEVER_SEEDED` 등재).
  ■ CHECK는 create_table 안 op.f() 최종 이름이다(alembic check가 CHECK를 못 보므로 정의문 테스트 tests/integration/test_order_board_constraints.py가 고정한다 — 함정 ①·⑪).
  ■ 멱등 UNIQUE는 `WHERE deleted_at IS NULL` 부분 인덱스다(§17.4). 식별자 63자 이내(최장 47자).
  ■ MUTABLE 표(이름·조건 수정·soft delete가 앱 계정의 정상 UPDATE) — REVOKE 없음.
  ■ downgrade는 인덱스 → 테이블 drop. 개인 설정 데이터는 함께 사라진다(되돌리기 비용 낮음 — ADR-0066).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2cb6020b2bb"
down_revision: str | None = "e7a3b9c1d4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "board_saved_filters",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("filter_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_board_saved_filters_name_nonblank")),
        sa.CheckConstraint(
            "jsonb_typeof(filter_config) = 'object'",
            name=op.f("ck_board_saved_filters_filter_config_is_object"),
        ),
        sa.CheckConstraint("name !~ '[[:cntrl:]]'", name=op.f("ck_board_saved_filters_name_clean")),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_board_saved_filters_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_board_saved_filters_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_board_saved_filters_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_board_saved_filters")),
    )
    op.create_index(
        "uq_board_saved_filters_user_id_name_active",
        "board_saved_filters",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_board_saved_filters_user_id_name_active",
        table_name="board_saved_filters",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("board_saved_filters")
