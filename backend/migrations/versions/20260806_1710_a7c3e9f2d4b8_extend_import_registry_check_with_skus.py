"""extend import registry check with skus

리비전 ID: a7c3e9f2d4b8
직전 리비전: b3f7a2c9d1e5
생성 시각(UTC): 2026-08-06 17:10:00+00:00

SKU 왕복 어댑터 추가(S1.5 판정 ① / 조건 8 SKU 왕복 스트레치 / §12.2 보강·
ADR-0030)에 따라 `import_staging.registry_code` CHECK 열거에 'skus'를 더한다 —
models.IMPORT_REGISTRY_CODES와 1:1(아키텍처 테스트가 대조).

체크리스트 (autogenerate 결과는 초안이다 — 사람이 전건 확인한다):
  ■ **기존 테이블 CHECK 변경은 autogenerate가 감지하지 못한다(함정 ①)** —
    이 파일 전체가 수기다. drop→create 재정의이고 op.f()가 필수다(함정 ⑪ —
    평문 이름을 주면 naming convention이 한 번 더 접두된다).
  ■ 구 정의(b3f7a2c9d1e5 원문):
      registry_code IN ('materials', 'partners')
    신 정의(models.value_in이 정렬한 것과 같은 열거):
      registry_code IN ('materials', 'partners', 'skus')
  ■ 정의문 실재는 pg_get_constraintdef 테스트가 지킨다
    (tests/integration/test_import_constraints.py — PR-2 선례 양식).
  ■ downgrade는 구 정의 복원이다 — 'skus' 스테이징 행이 이미 있으면 CHECK
    재생성이 실패한다(데이터가 제약보다 넓다). 그 실패는 올바른 실패다 —
    행을 지우는 것은 downgrade의 일이 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a7c3e9f2d4b8"
down_revision: str | None = "b3f7a2c9d1e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_import_staging_registry_code_valid"
_TABLE = "import_staging"
_OLD = "registry_code IN ('materials', 'partners')"
_NEW = "registry_code IN ('materials', 'partners', 'skus')"


def upgrade() -> None:
    op.drop_constraint(op.f(_NAME), _TABLE, type_="check")
    op.create_check_constraint(op.f(_NAME), _TABLE, _NEW)


def downgrade() -> None:
    op.drop_constraint(op.f(_NAME), _TABLE, type_="check")
    op.create_check_constraint(op.f(_NAME), _TABLE, _OLD)
