"""documents purged_at

리비전 ID: 9c2d5e7a1b34
직전 리비전: 1df4a398d38a
생성 시각(UTC): 2026-09-29 21:56:29.156241+00:00

문서 실물 물리 정리 기록 (S2-3 판정 요청 17 / s2-4-plan.md §2 안건 ⑧ (b) — ADR-0050):
  ① documents.purged_at TIMESTAMPTZ NULL — 소프트 삭제된 FILE 문서의 실물을 유예기간·보존기한 경과 뒤
     지웠다는 기록. 행은 지우지 않는다(이력·해시 보존).
  ② CHECK purged_requires_deleted_file — 소프트 삭제된 FILE 문서에만 값이 들어갈 수 있다(살아 있는
     문서의 실물을 지웠다는 기록 금지). 기존 테이블에 붙는 CHECK라 autogenerate가 감지하지 못한다
     (함정 ①) — 수기이며 op.f()를 붙였다(함정 ⑪).

체크리스트:
  ■ 컬럼 추가만 — rename·drop 없음. 기존 행은 전부 NULL(정리된 적 없음)이라 CHECK를 즉시 만족한다.
  ■ 시드·트리거·GRANT 변경 없음 — documents는 이미 MUTABLE(table_policy)이고 UPDATE 권한이 있다.
  ■ downgrade는 CHECK → 컬럼 순으로 되돌린다. 정리된 행이 있으면 컬럼과 함께 "정리됨" 기록이 사라진다
     (실물은 이미 없다 — 백업에서만 복원 가능).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2d5e7a1b34"
down_revision: str | None = "1df4a398d38a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_documents_purged_requires_deleted_file"),
        "documents",
        "purged_at IS NULL OR (deleted_at IS NOT NULL AND storage_kind = 'FILE')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_documents_purged_requires_deleted_file"), "documents", type_="check"
    )
    op.drop_column("documents", "purged_at")
