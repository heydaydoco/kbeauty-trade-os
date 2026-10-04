"""H·I·J·K. 승인 무결성 대사 잡 `approval-integrity-check` (S3-2 PR-1b / ADR-0087 / design-integrated §9 R-17 / PROGRESS PR-9a 부채 ①).

■ `scan_all` — `check_integrity`를 `after_id` 페이지로 **전건** 순회한다(페이지 경계에서 빠지거나 겹치는 승인 0).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.approvals import integrity
from app.modules.identity.models import RoleCode
from tests.factories.approvals import add_line, count, credit_so, make_user, request

pytestmark = pytest.mark.group_h


def _pending(n: int) -> list[int]:
    if count("approval_lines") == 0:
        add_line(0, role="TRADE")
    return [
        int(request(credit_so()["id"], make_user(RoleCode.TRADE)).approval.id) for _ in range(n)
    ]


def _forge(sql: str, **params: Any) -> None:
    """소유자 권한 위조 시뮬레이션 — 앱 경로를 거치지 않는 직접 변경."""
    with owner_engine.begin() as connection:
        connection.execute(text(sql), params)


# ── 전건 순회 (K) ────────────────────────────────────────────────────────────


@pytest.mark.group_k
@pytest.mark.parametrize("page_size", [1, 2, 4, 5, 1000])
def test_scan_all_visits_every_approval_across_page_boundaries(page_size: int) -> None:
    """페이지 크기(1·경계 정확히 맞음·경계 넘음·한 페이지)와 무관하게 대사 수 = 승인 전건, 마지막 페이지의 위조까지 찾는다"""
    ids = _pending(4)
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[-1])
    _forge("DELETE FROM approval_events WHERE approval_id = :i", i=ids[0])
    with unit_of_work() as uow:
        scanned, problems = integrity.scan_all(uow.session, page_size=page_size)
    assert scanned == 4
    assert problems == [
        {"approval_id": ids[0], "problem": "NO_EVENTS"},
        {"approval_id": ids[-1], "problem": "NO_EVENTS"},
    ]


@pytest.mark.group_k
def test_scan_all_on_an_empty_table_is_clean_and_rejects_a_zero_page() -> None:
    with unit_of_work() as uow:
        assert integrity.scan_all(uow.session) == (0, [])
        with pytest.raises(ValueError):
            integrity.scan_all(uow.session, page_size=0)
