"""promote msds and label files to documents

리비전 ID: d5e8f2a6c4b9
직전 리비전: c9a2e4f7b1d3
생성 시각(UTC): 2026-08-06 03:10:00.000000+00:00

임시 문자열 2건의 documents 승격 (ADR-0020 / [M1] 보강(S1-1) ⑤·(S1-2) ⑧ /
부채 #14 잔여 2건 종결): `skus.msds_url` → documents(SKU×MSDS, LINK),
`labels.file_url` → documents(LABEL×LABEL_ARTWORK, LINK).

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ **rename이 아니라 승격**이다 — drop 전에 backfill이 값을 documents 행으로
    옮긴다(트림, 빈 문자열 제외, 500자 절단 없음 — 원본 컬럼도 500자였다).
    자동 생성 행은 note에 출처를 남긴다(ADR-0016 A2 관례, dev 전량 보고 대상).
  ■ ★ 함정 ① 해당 확정분: `set_has_no_dg` CHECK가 msds_url을 포함한다.
    autogenerate는 기존 테이블의 CHECK 변경을 감지하지 못하므로 **drop 컬럼
    전에 CHECK를 지우고, 지운 뒤 msds_url 없는 정의로 손으로 재생성**한다.
    모델(catalog/models.py)과 1:1 대조 완료, 제약 실재는
    tests/integration/test_sku_constraints.py가 지킨다.
  ■ labels.file_url은 어떤 CHECK에도 없다(실측 — 라벨 CHECK는 country_code
    형식·approval_status 열거·label_version>0뿐).
  ■ "SET SKU에 MSDS 금지"의 DB CHECK 수기 재정의는 **하지 않는다** — 웹 세션
    승인이 서비스 가드+테스트로 대체를 판정했다(문서는 다른 테이블이라 CHECK로
    표현 불가). 이관분은 구 CHECK가 SET의 msds_url NULL을 보장했으므로 안전하다.
  ■ downgrade는 역복사다(승인 문면) — 문자열 컬럼을 되살려 LINK형 이관분
    문서의 URL을 결정적으로(최소 id) 복사한 뒤 구 CHECK를 복원한다. FILE형
    문서는 URL이 없어 역복사 대상이 아니다(테이블 자체는 직전 리비전의
    downgrade가 지운다 — 저장 파일은 디스크에 남는다).
  ■ 백필 실측 테스트: tests/integration/test_migrations.py (직전 리비전까지
    올려 행을 심고 head로 — S1-1·S1-3 PR-1 백필과 같은 하네스).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d5e8f2a6c4b9"
down_revision: str | None = "c9a2e4f7b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 구 CHECK — 7c4162d3ceb1이 만든 원문 그대로(msds_url 포함). downgrade가 복원한다.
_SET_HAS_NO_DG_OLD = (
    "kind <> 'SET' OR ("
    " dg_flag = false AND un_number IS NULL AND dg_class IS NULL"
    " AND packing_group IS NULL AND flash_point_c IS NULL"
    " AND alcohol_content_pct IS NULL AND is_aerosol = false"
    " AND is_limited_quantity = false AND msds_url IS NULL)"
)

#: 신 CHECK — msds_url 절만 뺐다. 모델(catalog/models.py set_has_no_dg)과 1:1.
_SET_HAS_NO_DG_NEW = (
    "kind <> 'SET' OR ("
    " dg_flag = false AND un_number IS NULL AND dg_class IS NULL"
    " AND packing_group IS NULL AND flash_point_c IS NULL"
    " AND alcohol_content_pct IS NULL AND is_aerosol = false"
    " AND is_limited_quantity = false)"
)


def upgrade() -> None:
    # 1) backfill — 값이 있는 행만 LINK형 문서로 이관한다. 출처를 note에 남겨
    #    dev 자동 생성분 전량 보고(웹 세션 승인 조건 3)가 이 표식으로 선다.
    op.execute(
        """
        INSERT INTO documents (owner_type, owner_id, document_type_id, storage_kind, url, note)
        SELECT 'SKU', s.id,
               (SELECT id FROM document_types
                WHERE code = 'MSDS' AND deleted_at IS NULL),
               'LINK', btrim(s.msds_url),
               'MIG: skus.msds_url 이관 (ADR-0020)'
        FROM skus s
        WHERE s.msds_url IS NOT NULL AND btrim(s.msds_url) <> ''
        """
    )
    op.execute(
        """
        INSERT INTO documents (owner_type, owner_id, document_type_id, storage_kind, url, note)
        SELECT 'LABEL', l.id,
               (SELECT id FROM document_types
                WHERE code = 'LABEL_ARTWORK' AND deleted_at IS NULL),
               'LINK', btrim(l.file_url),
               'MIG: labels.file_url 이관 (ADR-0020 부기)'
        FROM labels l
        WHERE l.file_url IS NOT NULL AND btrim(l.file_url) <> ''
        """
    )

    # 2) skus.msds_url 제거 — CHECK가 컬럼을 물고 있어 순서가 강제다:
    #    구 CHECK drop → 컬럼 drop → 신 CHECK 수기 재생성(함정 ①).
    #    ★ op.f() 필수 — 평문 이름은 naming convention이 한 번 더 접두된다.
    op.drop_constraint(op.f("ck_skus_set_has_no_dg"), "skus", type_="check")
    op.drop_column("skus", "msds_url")
    op.create_check_constraint(op.f("ck_skus_set_has_no_dg"), "skus", _SET_HAS_NO_DG_NEW)

    # 3) labels.file_url 제거 — 값은 위에서 전부 documents로 옮겨졌다(ADR-0020
    #    "'이미 값이 있으니까'로 넘어가는" 영구 잔류 경로의 차단).
    op.drop_column("labels", "file_url")


def downgrade() -> None:
    # 역복사(웹 세션 승인 문면) — 문자열 컬럼을 되살려 LINK형 이관분의 URL을
    # 복사한 뒤 구 CHECK를 복원한다. 소유자당 여러 건이면 최소 id로 결정적이게.
    op.add_column(
        "skus", sa.Column("msds_url", sa.VARCHAR(length=500), autoincrement=False, nullable=True)
    )
    op.drop_constraint(op.f("ck_skus_set_has_no_dg"), "skus", type_="check")
    op.execute(
        """
        UPDATE skus s SET msds_url = left(sub.url, 500)
        FROM (
            SELECT DISTINCT ON (d.owner_id) d.owner_id, d.url
            FROM documents d
            JOIN document_types t ON t.id = d.document_type_id
            WHERE d.owner_type = 'SKU' AND t.code = 'MSDS'
              AND d.storage_kind = 'LINK' AND d.deleted_at IS NULL
            ORDER BY d.owner_id, d.id
        ) sub
        WHERE s.id = sub.owner_id AND s.kind <> 'SET'
        """
    )
    op.create_check_constraint(op.f("ck_skus_set_has_no_dg"), "skus", _SET_HAS_NO_DG_OLD)

    op.add_column(
        "labels", sa.Column("file_url", sa.VARCHAR(length=500), autoincrement=False, nullable=True)
    )
    op.execute(
        """
        UPDATE labels l SET file_url = left(sub.url, 500)
        FROM (
            SELECT DISTINCT ON (d.owner_id) d.owner_id, d.url
            FROM documents d
            JOIN document_types t ON t.id = d.document_type_id
            WHERE d.owner_type = 'LABEL' AND t.code = 'LABEL_ARTWORK'
              AND d.storage_kind = 'LINK' AND d.deleted_at IS NULL
            ORDER BY d.owner_id, d.id
        ) sub
        WHERE l.id = sub.owner_id
        """
    )
