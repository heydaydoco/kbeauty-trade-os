"""K. users를 가리키는 모든 FK 컬럼은 이관 대상·이력·신원 연결 중 하나로 분류돼 있다 (ADR-0067).

담당 이관 등록 테스트(test_assignment_coverage)는 컬럼 **이름**으로 담당자를 탐지한다.
`approver_id`처럼 이름이 탐지 집합 밖이면 조용히 빠지므로, 이름이 아니라 "users FK인가"로
전수 분류를 강제한다.
"""

from __future__ import annotations

import pytest

import app.registry  # noqa: F401 — 모든 모델 등록
from app.core.db.base import Base
from app.modules.handover.targets import (
    ASSIGNMENT_TARGETS,
    AUDIT_USER_FK_COLUMNS,
    USER_FK_CLASSIFICATION,
)

pytestmark = pytest.mark.group_k

_ALLOWED = {"ACTOR_LOG", "IDENTITY_LINK"}


def _user_fk_columns() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == "users" and fk.parent.name not in AUDIT_USER_FK_COLUMNS:
                found.add((table.name, fk.parent.name))
    return found


def test_scan_is_not_vacuous() -> None:
    """검사 대상이 실제로 있다(빈 목록으로 초록을 사지 않는다)"""
    assert len(_user_fk_columns()) >= 6


def test_every_user_fk_is_a_handover_target_or_classified() -> None:
    """users FK 컬럼은 이관 대상이거나 분류표에 있어야 한다"""
    targets = {(t.model.__tablename__, t.column.key) for t in ASSIGNMENT_TARGETS}
    unclassified = _user_fk_columns() - targets - set(USER_FK_CLASSIFICATION)
    assert not unclassified, (
        f"분류되지 않은 users FK: {sorted(unclassified)}\n"
        "담당자면 handover/targets.py ASSIGNMENT_TARGETS에, 이력·신원 연결이면 "
        "USER_FK_CLASSIFICATION에 등록하세요."
    )


def test_classification_has_no_stale_or_invalid_entries() -> None:
    """분류표에 없어진 컬럼·잘못된 값·이관 대상과의 중복이 없다"""
    live = _user_fk_columns()
    targets = {(t.model.__tablename__, t.column.key) for t in ASSIGNMENT_TARGETS}
    assert set(USER_FK_CLASSIFICATION) <= live, "분류표에 더는 없는 컬럼이 있다"
    assert not (set(USER_FK_CLASSIFICATION) & targets), "이관 대상이면서 분류표에도 있다"
    assert set(USER_FK_CLASSIFICATION.values()) <= _ALLOWED
