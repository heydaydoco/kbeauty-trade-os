"""J. 안전 계약 — audit_log 불변 (DESIGN.md §17.5 / S0-2 DoD / GC-A4 계열).

권한 목록 검사(test_table_policy.py)와 별개로 **실제로 UPDATE를 때려 보고 DB가
거부하는지**를 확인한다. 권한 조회가 맞아도 트리거·소유자 설정이 어긋나 있으면
실제 거부는 일어나지 않을 수 있고, DoD가 요구하는 것은 "거부된다"는 사실이다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.db.session import engine
from app.core.db.uow import unit_of_work
from app.modules.audit.models import AuditLog

pytestmark = pytest.mark.group_j

PERMISSION_DENIED = "42501"


def _insert_one() -> int:
    with unit_of_work() as uow:
        row = AuditLog(action="test.audit.probe", detail={"note": "불변 검사용"})
        uow.session.add(row)
        uow.session.flush()
        return row.id


def test_app_account_can_insert_audit_log() -> None:
    """앱 계정은 감사 로그를 남길 수 있다 (기록까지 막으면 감사 자체가 불가능)"""
    row_id = _insert_one()
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT action FROM audit_log WHERE id = :id"), {"id": row_id}
        ).scalar_one()
    assert found == "test.audit.probe"


def test_app_account_cannot_update_audit_log() -> None:
    """앱 계정의 audit_log UPDATE는 DB가 거부한다 (42501)"""
    row_id = _insert_one()
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(
            text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"), {"id": row_id}
        )
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED


def test_app_account_cannot_delete_audit_log() -> None:
    """앱 계정의 audit_log DELETE는 DB가 거부한다 (42501)"""
    row_id = _insert_one()
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": row_id})
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED


def test_app_account_cannot_truncate_audit_log() -> None:
    """앱 계정의 audit_log TRUNCATE도 거부한다 (DELETE만 막으면 우회로가 남는다)"""
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(text("TRUNCATE audit_log"))
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED
