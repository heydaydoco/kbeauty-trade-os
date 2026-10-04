"""s32_holidays

리비전 ID: 394c76a7d2b7
직전 리비전: f2cb6020b2bb
생성 시각(UTC): 2026-10-04 05:19:11+00:00

S3-2 PR-2a 휴일 캘린더 — M13 (ADR-0082 / design-B §B11 / design-integrated §2.1 (j)(k)·§2.12·§9 R-24):
  holiday_calendar_years — 국가×연도 적재 선언(국가 CHAR(2) `^[A-Z]{2}$` — **markets FK 아님**·연도 2000~2999·근거 링크 http(s)·확인일 필수·version).
    활성 선언 유일 = 부분 유니크 uq_holiday_calendar_years_country_code_year_active, 복합 FK 대상 UNIQUE(id, country_code, year).
  holidays — 선언에 속한 휴일(날짜·이름). **연도·국가 정합 DB 강제(R-24)**: `year` NOT NULL + 복합 FK
    (calendar_year_id, country_code, year) → holiday_calendar_years(id, country_code, year) + CHECK extract(year FROM holiday_on) = year.
    활성 휴일 유일 = 부분 유니크 uq_holidays_country_code_holiday_on_active.

체크리스트:
  ■ 신규 테이블 2개만(백필·기존 테이블 변경 없음). **시드 0** — 휴일은 근거를 사람이 확인한 선언만(외부 자동 수집 0, 함정 ⑩·`_NEVER_SEEDED` 등재).
  ■ CHECK는 create_table 안 op.f() 최종 이름이다(alembic check가 CHECK를 못 보므로 정의문 시험 tests/integration/test_holiday_constraints.py가 고정 — 함정 ①·⑪).
  ■ **복합 FK 이름은 직접 지정**한다: 명명 규칙 자동 이름 `fk_holidays_calendar_year_id_country_code_year_holiday_calendar_years`는 69자로
    63자 상한을 넘는다(PR-1 §8 ④ 실측) → `fk_holidays_calendar_year`(25자). 그 밖의 식별자 최장 50자(`uq_holiday_calendar_years_country_code_year_active`).
  ■ 멱등 UNIQUE는 `WHERE deleted_at IS NULL` 부분 인덱스다(§17.4). 복합 FK 대상 UNIQUE는 전체 유니크(id 포함이라 부분 조건 불요).
  ■ MUTABLE 2표(원자 교체가 선언 갱신·휴일 soft delete를 앱 계정의 정상 UPDATE로 한다) — REVOKE 없음. 이력 정본은 audit_log.
  ■ downgrade는 holidays(자식) → holiday_calendar_years 순 drop. 선언 데이터는 함께 사라진다(되돌리기 비용 낮음 — M13 병합 전).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "394c76a7d2b7"
down_revision: str | None = "f2cb6020b2bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holiday_calendar_years",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("country_code", sa.CHAR(length=2), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("verified_on", sa.Date(), nullable=False),
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
            "country_code ~ '^[A-Z]{2}$'",
            name=op.f("ck_holiday_calendar_years_country_code_format"),
        ),
        sa.CheckConstraint(
            "source_url !~ '[[:cntrl:][:space:]]'",
            name=op.f("ck_holiday_calendar_years_source_url_clean"),
        ),
        sa.CheckConstraint(
            "source_url ~ '^https?://'", name=op.f("ck_holiday_calendar_years_source_url_http")
        ),
        sa.CheckConstraint(
            "year BETWEEN 2000 AND 2999", name=op.f("ck_holiday_calendar_years_year_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_holiday_calendar_years_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_holiday_calendar_years_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday_calendar_years")),
        sa.UniqueConstraint(
            "id",
            "country_code",
            "year",
            name=op.f("uq_holiday_calendar_years_id_country_code_year"),
        ),
    )
    op.create_index(
        "uq_holiday_calendar_years_country_code_year_active",
        "holiday_calendar_years",
        ["country_code", "year"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "holidays",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("calendar_year_id", sa.BigInteger(), nullable=False),
        sa.Column("country_code", sa.CHAR(length=2), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("holiday_on", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
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
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_holidays_name_nonblank")),
        sa.CheckConstraint(
            "country_code ~ '^[A-Z]{2}$'", name=op.f("ck_holidays_country_code_format")
        ),
        sa.CheckConstraint("name !~ '[[:cntrl:]]'", name=op.f("ck_holidays_name_clean")),
        sa.CheckConstraint(
            "extract(year FROM holiday_on) = year", name=op.f("ck_holidays_day_in_year")
        ),
        sa.ForeignKeyConstraint(
            ["calendar_year_id", "country_code", "year"],
            [
                "holiday_calendar_years.id",
                "holiday_calendar_years.country_code",
                "holiday_calendar_years.year",
            ],
            name="fk_holidays_calendar_year",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_holidays_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_holidays_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holidays")),
    )
    op.create_index("ix_holidays_calendar_year_id", "holidays", ["calendar_year_id"], unique=False)
    op.create_index(
        "uq_holidays_country_code_holiday_on_active",
        "holidays",
        ["country_code", "holiday_on"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_holidays_country_code_holiday_on_active",
        table_name="holidays",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_holidays_calendar_year_id", table_name="holidays")
    op.drop_table("holidays")
    op.drop_index(
        "uq_holiday_calendar_years_country_code_year_active",
        table_name="holiday_calendar_years",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("holiday_calendar_years")
