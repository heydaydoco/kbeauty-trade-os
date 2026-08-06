"""import staging and rows

리비전 ID: b3f7a2c9d1e5
직전 리비전: d5e8f2a6c4b9
생성 시각(UTC): 2026-08-06 09:00:00.000000+00:00

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ rename 없음 — 신규 테이블 2개뿐이고 기존 테이블 ALTER가 없다(함정 ① 비해당).
  ■ CHECK 13건 전부 create_table 안에 포함됐다. 모델과 1:1 대조 완료:
    import_staging 8(레지스트리 2값·상태 2값·sha256 형식·행 수 2건 비음수·
    무변경≤전체·확정 시각/확정자 쌍 2건) / import_staging_rows 5(kind 3값·
    행번호≥2·NEW 대상 없음·CHANGED 대상 필수·ERROR 사유 필수).
  ■ 멱등 UNIQUE: uq_import_staging_rows_staging_id_row_no_active(47자)는
    unique_active 표준형. ★ uq_import_staging_registry_code_sha256_pending
    (46자)은 **조건이 다른 수기 부분 유니크**다(WHERE deleted_at IS NULL AND
    status = 'PENDING') — 같은 파일은 검토 대기 중 한 번만, 확정 후 재업로드는
    허용(ADR-09 파일 해시 멱등의 M9 구현형). 모델 __table_args__와 동일 조건.
  ■ server_default: 믹스인(now()·version=1) + status 'PENDING' + payload '{}'
    2건이 도메인 기본값이다 — labels.approval_status 'DRAFT'와 같은 관례.
  ■ import_staging_rows에는 version 컬럼이 **없다** — 행은 등록 후 불변이라
    수정 경로가 없다(UserRole 등 이력성 테이블과 같은 꼴, 모델 독스트링).
  ■ downgrade가 인덱스·테이블 전부 되돌린다 — CI 드라이런 왕복 대상.
  ■ GRANT/REVOKE 없음 — 둘 다 MUTABLE(table_policy 등록: 확정=UPDATE,
    파기=soft delete). 시드 없음 — PRESERVED_TABLES 비대상.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b3f7a2c9d1e5"
down_revision: str | None = "d5e8f2a6c4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_staging",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("registry_code", sa.String(length=20), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("total_data_rows", sa.Integer(), nullable=False),
        sa.Column("unchanged_rows", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_id", sa.BigInteger(), nullable=True),
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
            "registry_code IN ('materials', 'partners')",
            name=op.f("ck_import_staging_registry_code_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('CONFIRMED', 'PENDING')", name=op.f("ck_import_staging_status_valid")
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_import_staging_sha256_format")
        ),
        sa.CheckConstraint(
            "total_data_rows >= 0", name=op.f("ck_import_staging_total_data_rows_nonnegative")
        ),
        sa.CheckConstraint(
            "unchanged_rows >= 0", name=op.f("ck_import_staging_unchanged_rows_nonnegative")
        ),
        sa.CheckConstraint(
            "unchanged_rows <= total_data_rows",
            name=op.f("ck_import_staging_unchanged_within_total"),
        ),
        sa.CheckConstraint(
            "(status = 'CONFIRMED') = (confirmed_at IS NOT NULL)",
            name=op.f("ck_import_staging_confirmed_at_pair"),
        ),
        sa.CheckConstraint(
            "(status = 'CONFIRMED') = (confirmed_by_id IS NOT NULL)",
            name=op.f("ck_import_staging_confirmed_by_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_id"],
            ["users.id"],
            name=op.f("fk_import_staging_confirmed_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_import_staging_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_import_staging_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_staging")),
    )
    # ── 손으로 쓴 부분 (autogenerate가 만들지 않는 것들) ──
    # 같은 파일은 검토 대기 중 한 번만 — 조건이 표준형(unique_active)과 달라 수기다.
    op.create_index(
        "uq_import_staging_registry_code_sha256_pending",
        "import_staging",
        ["registry_code", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'PENDING'"),
    )
    op.create_table(
        "import_staging_rows",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("staging_id", sa.BigInteger(), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
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
            "kind IN ('CHANGED', 'ERROR', 'NEW')", name=op.f("ck_import_staging_rows_kind_valid")
        ),
        sa.CheckConstraint("row_no >= 2", name=op.f("ck_import_staging_rows_row_no_after_header")),
        sa.CheckConstraint(
            "kind <> 'NEW' OR (target_id IS NULL AND expected_version IS NULL)",
            name=op.f("ck_import_staging_rows_new_has_no_target"),
        ),
        sa.CheckConstraint(
            "kind <> 'CHANGED' OR (target_id IS NOT NULL AND expected_version IS NOT NULL)",
            name=op.f("ck_import_staging_rows_changed_has_target"),
        ),
        sa.CheckConstraint(
            "kind <> 'ERROR' OR error_reason IS NOT NULL",
            name=op.f("ck_import_staging_rows_error_has_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_import_staging_rows_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["staging_id"],
            ["import_staging.id"],
            name=op.f("fk_import_staging_rows_staging_id_import_staging"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_import_staging_rows_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_staging_rows")),
    )
    op.create_index(
        "uq_import_staging_rows_staging_id_row_no_active",
        "import_staging_rows",
        ["staging_id", "row_no"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_import_staging_rows_staging_id_row_no_active",
        table_name="import_staging_rows",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("import_staging_rows")
    op.drop_index(
        "uq_import_staging_registry_code_sha256_pending",
        table_name="import_staging",
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'PENDING'"),
    )
    op.drop_table("import_staging")
