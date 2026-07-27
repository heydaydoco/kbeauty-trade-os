"""K. 보안·품질 — 담당 이관 커버리지 (DESIGN.md §2 "담당 이관" / ADR-0015).

담당자 컬럼을 새로 만든 세션이 이관 대상 등록을 잊으면, 퇴사자의 그 담당 건만
아무도 모르게 남는다. **누락이 조용한** 전형적인 형태다 — 이관은 성공했다고
보고되고, 남은 건은 기일을 넘긴 뒤에야 발견된다.

그래서 매핑된 전 모델을 훑어 "담당자 컬럼을 가졌는데 등록되지 않은" 것이 하나라도
있으면 실패시킨다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from app.modules.handover.targets import (
    ASSIGNMENT_COLUMN_NAMES,
    ASSIGNMENT_TARGETS,
)
from app.registry import Base

pytestmark = [pytest.mark.group_k, pytest.mark.meta]


def _models_with_assignment_columns() -> dict[str, set[str]]:
    """{테이블명: {담당자 컬럼명}} — 매핑된 전 모델에서 찾는다."""
    found: dict[str, set[str]] = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table is None:  # pragma: no cover — 단일 테이블 상속 등
            continue
        columns = {
            column.name for column in table.columns if column.name in ASSIGNMENT_COLUMN_NAMES
        }
        if columns:
            found[table.name] = columns
    return found


def test_scan_is_not_vacuous() -> None:
    """검사할 담당자 컬럼이 실제로 존재한다"""
    assert _models_with_assignment_columns(), (
        "담당자 컬럼을 가진 모델이 하나도 없다 — 스캔 조건(ASSIGNMENT_COLUMN_NAMES)을 확인하세요"
    )


def test_every_assignment_column_is_a_handover_target() -> None:
    """담당자 컬럼을 가진 모든 테이블이 이관 대상에 등록돼 있다"""
    registered = {(target.label, target.column.key) for target in ASSIGNMENT_TARGETS}
    expected = {
        (table, column)
        for table, columns in _models_with_assignment_columns().items()
        for column in columns
    }
    missing = expected - registered
    assert not missing, (
        f"이관 대상에 없는 담당자 컬럼: {sorted(missing)}\n"
        "app/modules/handover/targets.py의 ASSIGNMENT_TARGETS에 추가하세요 — "
        "빠지면 퇴사자의 그 담당 건만 조용히 남습니다."
    )


def test_no_stale_handover_target() -> None:
    """사라진 컬럼이 이관 대상에 남아 있지 않다"""
    live = {
        (table, column)
        for table, columns in _models_with_assignment_columns().items()
        for column in columns
    }
    stale = {(target.label, target.column.key) for target in ASSIGNMENT_TARGETS} - live
    assert not stale, f"존재하지 않는 담당자 컬럼이 이관 대상에 있다: {sorted(stale)}"


def test_audit_columns_are_not_treated_as_assignments() -> None:
    """감사 컬럼은 이관 대상이 아니다

    created_by_id·updated_by_id·actor_user_id는 "누가 했는가"라는 **과거 사실**이다.
    담당이 바뀌었다고 과거에 누가 했는지가 바뀌면 감사 추적이 무너진다.
    """
    for name in ("created_by_id", "updated_by_id", "actor_user_id"):
        assert name not in ASSIGNMENT_COLUMN_NAMES

    labels = {target.column.key for target in ASSIGNMENT_TARGETS}
    assert not labels & {"created_by_id", "updated_by_id", "actor_user_id"}


def test_handover_targets_cover_the_tables_we_know_about() -> None:
    """S0-2 시점의 대상 3곳이 실제로 등록돼 있다 (등록소가 비지 않았다)"""
    labels = {target.label for target in ASSIGNMENT_TARGETS}
    assert {"tasks", "alert_rules", "alerts"} <= labels


def test_inspect_is_usable_on_registered_models() -> None:
    """등록된 모델이 실제 매핑된 ORM 클래스다 (문자열 오타 방지)"""
    for target in ASSIGNMENT_TARGETS:
        assert inspect(target.model).local_table is not None
