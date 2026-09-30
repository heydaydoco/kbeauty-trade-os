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


# --- S3-1 PR-2: 컬럼 단위 UPDATE 권한 (ADR-0060) ---------------------------------------------


class _OwnerOp:
    """마이그레이션의 `op.execute`를 owner 연결로 흉내 낸다."""

    def __init__(self, connection) -> None:  # type: ignore[no-untyped-def]
        self._connection = connection

    def execute(self, statement: str) -> None:
        self._connection.execute(text(statement))


def test_restrict_update_columns_is_enforced_by_the_database(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """허용 컬럼만 UPDATE되고 나머지·DELETE는 DB가 거부한다(앱 계정으로 실측)"""
    from sqlalchemy.exc import ProgrammingError

    from app.core.db import table_policy
    from app.core.db.session import engine

    allowed = frozenset({"status", "version"})
    monkeypatch.setitem(table_policy.COLUMN_UPDATE_ALLOWLIST, "zz_col_grant", allowed)
    with owner_engine.begin() as owner:
        owner.execute(
            text(
                "CREATE TABLE public.zz_col_grant (id int PRIMARY KEY, status text,"
                " version int, amount_frozen bigint)"
            )
        )
        owner.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON public.zz_col_grant TO kbos_app")
        )
        table_policy.restrict_update_columns(_OwnerOp(owner), "zz_col_grant", allowed)
        owner.execute(text("INSERT INTO public.zz_col_grant VALUES (1, 'A', 1, 100)"))
    try:
        with engine.begin() as app:
            app.execute(
                text("UPDATE public.zz_col_grant SET status = 'B', version = 2 WHERE id = 1")
            )
        for bad in (
            "UPDATE public.zz_col_grant SET amount_frozen = 1 WHERE id = 1",
            "DELETE FROM public.zz_col_grant WHERE id = 1",
        ):
            with pytest.raises(ProgrammingError) as caught, engine.begin() as app:
                app.execute(text(bad))
            assert "permission denied" in str(caught.value.orig).lower(), bad
        with owner_engine.connect() as owner:
            assert owner.execute(
                text("SELECT status, amount_frozen FROM public.zz_col_grant WHERE id = 1")
            ).one() == ("B", 100)
    finally:
        with owner_engine.begin() as owner:
            owner.execute(text("DROP TABLE IF EXISTS public.zz_col_grant"))


def test_restrict_update_columns_rejects_unregistered_or_immutable() -> None:
    """등록 없는 테이블·불변 테이블·빈 허용 목록은 마이그레이션에서 즉시 실패한다"""
    from app.core.db.table_policy import restrict_update_columns

    class _Op:
        def execute(self, statement: str) -> None:  # pragma: no cover
            raise AssertionError("실행되면 안 된다")

    with pytest.raises(ValueError, match="COLUMN_UPDATE_ALLOWLIST"):
        restrict_update_columns(_Op(), "not_registered", frozenset({"a"}))
    with pytest.raises(ValueError, match="비어"):
        restrict_update_columns(_Op(), "x", frozenset())
    with pytest.raises(ValueError, match="IMMUTABLE"):
        restrict_update_columns(_Op(), "audit_log", frozenset({"a"}))
