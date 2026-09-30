"""S3-1 마스터 보강 — partners 영문명·영문주소, skus MOQ

리비전 ID: a1f4c7e2b9d3
직전 리비전: 9c2d5e7a1b34

S3-1 PR-3 (ADR-0056 — 설계 design-A A8·A11):
  ① partners.name_en VARCHAR(200) NULL, partners.address_en VARCHAR(500) NULL — QT·PI 서류의
     바이어 표기 원천. 전표는 발행 시점에 값 복사로 스냅샷하며 이 컬럼을 다시 읽지 않는다.
  ② skus.moq INTEGER NULL + CHECK moq_positive(moq > 0) — 최소주문수량(EA, 세트는 세트 단위).
     NULL = 미정의. 기존 테이블에 붙는 CHECK라 autogenerate가 감지하지 못한다(함정 ①) —
     수기이며 op.f()를 붙였다(함정 ⑪).

체크리스트:
  ■ 컬럼 추가만 — rename·drop 없음. 기존 행은 전부 NULL이라 CHECK를 즉시 만족한다.
  ■ 시드·트리거·GRANT 변경 없음(partners·skus는 이미 MUTABLE).
  ■ downgrade는 CHECK → 컬럼 순으로 되돌린다. 값이 있으면 컬럼과 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f4c7e2b9d3"
down_revision: str | None = "9c2d5e7a1b34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("partners", sa.Column("name_en", sa.String(length=200), nullable=True))
    op.add_column("partners", sa.Column("address_en", sa.String(length=500), nullable=True))
    op.add_column("skus", sa.Column("moq", sa.Integer(), nullable=True))
    op.create_check_constraint(op.f("ck_skus_moq_positive"), "skus", "moq > 0")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_skus_moq_positive"), "skus", type_="check")
    op.drop_column("skus", "moq")
    op.drop_column("partners", "address_en")
    op.drop_column("partners", "name_en")
