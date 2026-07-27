"""J. 안전 계약 — 테이블 분류 완전성 (DESIGN.md §17.5).

S4-1에서 stock_movements의 권한 회수를 빠뜨리면 아무 증상 없이 지나간다.
그 누락을 **지금부터** 기계가 잡게 해 둔다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.table_policy import IMMUTABLE_TABLES, MUTABLE_TABLES, classified_tables

pytestmark = pytest.mark.group_j


def _live_tables() -> set[str]:
    with owner_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
            )
        ).scalars()
        return set(rows)


def test_policy_is_not_vacuous() -> None:
    """검사할 테이블이 실제로 존재한다 (빈 목록으로 초록을 사지 않는다)"""
    assert _live_tables(), "public 스키마에 테이블이 하나도 없다 — 이 검사는 무의미하다"


def test_every_table_is_classified() -> None:
    """새로 만든 테이블은 불변/가변 중 하나로 반드시 분류돼 있어야 한다"""
    unclassified = _live_tables() - classified_tables()
    assert not unclassified, (
        f"분류되지 않은 테이블: {sorted(unclassified)}\n"
        "app/core/db/table_policy.py의 IMMUTABLE_TABLES 또는 MUTABLE_TABLES에 "
        "추가하세요. 불변이라면 마이그레이션에서 revoke_mutations()도 함께 부릅니다(§17.5)."
    )


def test_no_stale_classification() -> None:
    """이미 없어진 테이블이 분류 목록에 남아 있지 않다"""
    stale = classified_tables() - _live_tables()
    assert not stale, f"존재하지 않는 테이블이 분류 목록에 남아 있다: {sorted(stale)}"


def test_immutable_tables_have_no_app_write_grants() -> None:
    """불변 테이블에는 앱 계정의 UPDATE/DELETE 권한이 없다"""
    # S0-2에서 audit_log가 첫 불변 테이블이 됐다. 목록이 비면 분류가 지워진 것이므로
    # 조용히 skip하지 않고 실패시킨다(빈 목록으로 초록을 사지 않는다).
    assert IMMUTABLE_TABLES, "불변 테이블이 하나도 없다 — table_policy.py의 분류가 지워졌는가?"
    with owner_engine.connect() as connection:
        for table in sorted(IMMUTABLE_TABLES):
            for privilege in ("UPDATE", "DELETE"):
                granted = connection.execute(
                    text("SELECT has_table_privilege('kbos_app', :t, :p)"),
                    {"t": f"public.{table}", "p": privilege},
                ).scalar_one()
                assert granted is False, f"{table}에 kbos_app의 {privilege} 권한이 남아 있다"


def test_no_table_is_in_both_sets() -> None:
    """한 테이블이 불변이면서 동시에 가변일 수는 없다"""
    assert not (IMMUTABLE_TABLES & MUTABLE_TABLES)
