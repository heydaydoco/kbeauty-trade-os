"""K. 임포트 확정의 다중 행 잠금은 id 오름차순이다 — 교착 회피(S3-1 PR-14a B2 / trade_docs.locking 문서).

`load_targets_for_update`가 만드는 SQL을 실제로 컴파일해 `ORDER BY … id`와 `FOR UPDATE`가 함께 있는지 본다(DB 없음).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.imports.registry import IMPORT_TARGETS

pytestmark = pytest.mark.group_k


class _CaptureSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    def execute(self, statement: Any) -> Any:
        self.statements.append(statement)

        class _Result:
            def scalars(self) -> list[Any]:
                return []

        return _Result()


@pytest.mark.parametrize("code", sorted(IMPORT_TARGETS))
def test_every_import_target_locks_rows_in_id_order(code: str) -> None:
    """레지스트리 전 대상(거래처·자재·SKU)의 확정 잠금 SQL이 `ORDER BY <표>.id`와 `FOR UPDATE`를 함께 갖는다"""
    session = _CaptureSession()
    IMPORT_TARGETS[code].load_targets_for_update(session, [3, 1, 2])  # type: ignore[arg-type]
    assert len(session.statements) == 1
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    order = sql.split("ORDER BY", 1)
    assert len(order) == 2 and ".id" in order[1].split("FOR UPDATE")[0], sql
