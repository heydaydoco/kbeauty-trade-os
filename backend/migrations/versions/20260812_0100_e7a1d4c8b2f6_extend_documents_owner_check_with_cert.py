"""extend documents owner check with certification

리비전 ID: e7a1d4c8b2f6
직전 리비전: 3e93b9fc667d
생성 시각(UTC): 2026-08-12 01:00:00+00:00

certification_tasks 서류 링크의 실소비(S2-2 PR-2 — 판정 안건 ⑤)에 따라
`documents.owner_type` 소유 열거 CHECK에 'CERTIFICATION'을 더한다 —
models.DOCUMENT_OWNER_TYPES와 1:1(정의문 테스트가 대조). ADR-0028의
"소유 열거는 소비분 한정 CHECK 확장" 규율의 첫 적용이며, 확장은 이
1종까지다(GMP류 공용 서류의 소유 공백은 관찰 원장 등재 — 과확장 억제).

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ **기존 테이블 CHECK 변경은 autogenerate가 감지하지 못한다(함정 ①)** —
    이 파일 전체가 수기다. drop→create 재정의이고 op.f()가 필수다(함정 ⑪ —
    평문 이름을 주면 naming convention이 한 번 더 접두된다).
  ■ **컬럼 확폭 동반**: 'CERTIFICATION'은 13자라 owner_type VARCHAR(10)에
    들어가지 않는다 — CHECK보다 먼저 10→13으로 넓힌다(모델 String(13)과
    일치, drift 검사(compare_type=True)가 대조).
  ■ 구 정의(c9a2e4f7b1d3 원문):
      owner_type IN ('LABEL', 'SKU')
    신 정의(models.value_in이 정렬한 것과 같은 열거):
      owner_type IN ('CERTIFICATION', 'LABEL', 'SKU')
  ■ 정의문 실재는 pg_get_constraintdef 테스트가 지킨다
    (tests/integration/test_document_constraints.py — a7c3e9f2d4b8 선례 양식).
  ■ downgrade는 구 정의·구 폭 복원이다 — 'CERTIFICATION' 소유 행이 이미
    있으면 CHECK 재생성·VARCHAR(10) 축소가 실패한다(데이터가 제약보다
    넓다). 그 실패는 올바른 실패다 — 행을 지우는 것은 downgrade의 일이
    아니다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1d4c8b2f6"
down_revision: str | None = "3e93b9fc667d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_documents_owner_type_valid"
_TABLE = "documents"
_OLD = "owner_type IN ('LABEL', 'SKU')"
_NEW = "owner_type IN ('CERTIFICATION', 'LABEL', 'SKU')"


def upgrade() -> None:
    op.alter_column(
        _TABLE,
        "owner_type",
        type_=sa.String(13),
        existing_type=sa.String(10),
        existing_nullable=False,
    )
    op.drop_constraint(op.f(_NAME), _TABLE, type_="check")
    op.create_check_constraint(op.f(_NAME), _TABLE, _NEW)


def downgrade() -> None:
    op.drop_constraint(op.f(_NAME), _TABLE, type_="check")
    op.alter_column(
        _TABLE,
        "owner_type",
        type_=sa.String(10),
        existing_type=sa.String(13),
        existing_nullable=False,
    )
    op.create_check_constraint(op.f(_NAME), _TABLE, _OLD)
