"""document types documents item profile document types

리비전 ID: c9a2e4f7b1d3
직전 리비전: e45b79db6198
생성 시각(UTC): 2026-08-06 03:00:00.000000+00:00

문서 보관소 3테이블 + 종류 시드 (§4.7·§4.8 / ADR-0028·0029 / WBS S1-3 PR-2).

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ 신규 테이블 3개뿐, **기존 테이블 ALTER 없음** — msds_url·file_url 승격은
    다음 마이그레이션이다(함정 ① 해당 지점을 거기로 몰았다).
  ■ CHECK 9건 전부 create_table 안에 포함. 모델과 1:1 대조 완료:
    document_types 1(retention_years>0) / documents 8(소유 열거 2종·형태 열거·
    FILE/LINK 상호 배타·size>0·sha256/stored_name 형식·기간 순서 2건).
  ■ 멱등 UNIQUE 3건 전부 부분 인덱스(WHERE deleted_at IS NULL). 최장 이름
    50자(uq_item_profile_document_types_profile_type_active — unique_active가
    만들 이름이 63자 상한을 넘어 모델 쪽에서 손으로 줄였다). ★ 유일키에
    금액·민감 이름 없음(sha256은 SENSITIVE_KEYS 비대상 — 실측).
  ■ 시드 10행: §4.7 열거 9종 + LABEL_ARTWORK(§4.5 라벨 파일 — §4.7 열거 밖
    CC 추론 추가분, 완료 보고에 구분 표시). 각 행 note에 근거 절 번호.
    원산지 증빙만 retention_years=5(§4.7 "보존기한(발급일+5년)").
    id는 GENERATED ALWAYS라 지정하지 않는다(roles 시드와 같은 꼴).
  ■ 시드 테이블은 tests/conftest.py PRESERVED_TABLES에 추가했다(주의 인계 ⑨).
  ■ server_default는 믹스인 3종(now()·version=1)뿐 — 도메인 기본값 없음 확인.
  ■ downgrade가 인덱스·테이블 전부 되돌린다 — CI 드라이런 왕복 대상.
  ■ GRANT/REVOKE 없음 — 셋 다 MUTABLE(마스터, table_policy 등록).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c9a2e4f7b1d3"
down_revision: str | None = "e45b79db6198"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ★ document_types는 roles와 같은 지위(마이그레이션 소유 시드)라 Version·
    #   Actor 컬럼이 없다 — users FK가 있으면 테스트 하네스의 TRUNCATE users
    #   CASCADE가 시드를 함께 지운다(모델 독스트링 참조).
    op.create_table(
        "document_types",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name_ko", sa.String(length=100), nullable=False),
        sa.Column("retention_years", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "retention_years > 0", name=op.f("ck_document_types_retention_years_positive")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_types")),
    )
    op.create_index(
        "uq_document_types_code_active",
        "document_types",
        ["code"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("owner_type", sa.String(length=10), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("document_type_id", sa.BigInteger(), nullable=False),
        sa.Column("storage_kind", sa.String(length=10), nullable=False),
        sa.Column("stored_name", sa.String(length=64), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
            "owner_type IN ('LABEL', 'SKU')", name=op.f("ck_documents_owner_type_valid")
        ),
        sa.CheckConstraint(
            "storage_kind IN ('FILE', 'LINK')", name=op.f("ck_documents_storage_kind_valid")
        ),
        sa.CheckConstraint(
            "(storage_kind = 'FILE' AND stored_name IS NOT NULL"
            " AND original_filename IS NOT NULL AND content_type IS NOT NULL"
            " AND size_bytes IS NOT NULL AND sha256 IS NOT NULL AND url IS NULL)"
            " OR (storage_kind = 'LINK' AND url IS NOT NULL"
            " AND stored_name IS NULL AND original_filename IS NULL"
            " AND content_type IS NULL AND size_bytes IS NULL AND sha256 IS NULL)",
            name=op.f("ck_documents_storage_kind_fields"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_documents_size_bytes_positive")),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_documents_sha256_format")),
        sa.CheckConstraint(
            "stored_name ~ '^[0-9a-f]{32}$'", name=op.f("ck_documents_stored_name_format")
        ),
        sa.CheckConstraint(
            "valid_until >= issued_on", name=op.f("ck_documents_validity_after_issue")
        ),
        sa.CheckConstraint(
            "retention_until >= issued_on", name=op.f("ck_documents_retention_after_issue")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_documents_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_documents_document_type_id_document_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_documents_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        "uq_documents_owner_type_owner_id_sha256_active",
        "documents",
        ["owner_type", "owner_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "item_profile_document_types",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("item_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("document_type_id", sa.BigInteger(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_item_profile_document_types_created_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            name=op.f("fk_item_profile_document_types_document_type_id_document_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_profile_id"],
            ["item_profiles.id"],
            name=op.f("fk_item_profile_document_types_item_profile_id_item_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_item_profile_document_types_updated_by_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_item_profile_document_types")),
    )
    op.create_index(
        "uq_item_profile_document_types_profile_type_active",
        "item_profile_document_types",
        ["item_profile_id", "document_type_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── 손으로 쓴 부분 (autogenerate가 만들지 않는 것들) ────────────────────

    # 문서 종류 시드 — §4.7 열거 9종 + LABEL_ARTWORK(§4.5, CC 추론 추가분).
    # note에 근거 절 번호를 남겨 시드 추적성(웹 세션 승인 조건 6)을 데이터에
    # 각인한다. id는 GENERATED ALWAYS라 지정하지 않는다(roles 시드와 같은 꼴).
    op.execute(
        """
        INSERT INTO document_types (code, name_ko, retention_years, note) VALUES
          ('CFS', '자유판매증명서(CFS)', NULL, '§4.7·§5.6 공통 수출 서류'),
          ('GMP', 'GMP 증명', NULL, '§4.7·§5.6 공통 수출 서류'),
          ('COA', '시험성적서(CoA)', NULL, '§4.7·§5.6·§8.1(로트 CoA 링크)'),
          ('MSDS', 'MSDS', NULL, '§4.7·§4.1·§7.7(유효본 미첨부 차단 게이트, P4)'),
          ('CERTIFICATE', '인증서', NULL, '§4.7·§12.1'),
          ('TRADE_DOC', '무역서류', NULL, '§4.7(포괄 코드 — 전표별 세분은 소비 세션 몫)'),
          ('PURCHASE_CONFIRMATION', '구매확인서', NULL, '§4.7'),
          ('LC_ADVICE', 'L/C 통지서(MT700)', NULL, '§4.7·§7.10·§12.1'),
          ('ORIGIN_EVIDENCE', '원산지 증빙', 5, '§4.7 — 보존기한 발급일+5년, 파기 잠금'),
          ('LABEL_ARTWORK', '라벨 파일(아트웍)', NULL,
           '§4.5·ADR-0020 부기 — §4.7 열거 밖 CC 추론 추가분(라벨 파일 승격용)')
        """
    )


def downgrade() -> None:
    op.drop_index(
        "uq_item_profile_document_types_profile_type_active",
        table_name="item_profile_document_types",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("item_profile_document_types")
    op.drop_index(
        "uq_documents_owner_type_owner_id_sha256_active",
        table_name="documents",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("documents")
    op.drop_index(
        "uq_document_types_code_active",
        table_name="document_types",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("document_types")
