"""J. 안전 계약 — DB 역할 권한 (DESIGN.md §17.5 / ADR-0002).

런타임 계정이 테이블 소유자가 되는 순간 §17.5의 불변 강제가 통째로 무의미해진다.
그런데 그 상태에서도 앱은 멀쩡히 동작하기 때문에 사람이 눈치챌 방법이 없다.
그래서 상시 검사한다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.db.session import engine, owner_engine

pytestmark = pytest.mark.group_j

PERMISSION_DENIED = "42501"


def test_runtime_connects_as_app_role() -> None:
    """런타임은 kbos_app으로 붙는다 (kbos_owner가 아니다)"""
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == "kbos_app"


def test_runtime_role_is_not_superuser() -> None:
    """런타임 계정은 슈퍼유저가 아니다"""
    with engine.connect() as connection:
        is_super = connection.execute(
            text("SELECT usesuper FROM pg_user WHERE usename = current_user")
        ).scalar_one()
    assert is_super is False


def test_app_role_cannot_create_table() -> None:
    """앱 계정은 테이블을 만들 수 없다 — 소유자가 아니어야 REVOKE가 강제된다"""
    with engine.connect() as connection, pytest.raises(ProgrammingError) as caught:
        connection.execute(text("CREATE TABLE app_should_not_create (i int)"))
    assert getattr(caught.value.orig, "sqlstate", None) == PERMISSION_DENIED


def test_app_role_cannot_drop_owner_table() -> None:
    """앱 계정은 소유자가 만든 테이블을 지울 수 없다"""
    from tests.support.scratch import scratch_table

    with (
        scratch_table() as table,
        engine.connect() as connection,
        pytest.raises(ProgrammingError),
    ):
        connection.execute(text(f'DROP TABLE public."{table}"'))


def test_migration_role_owns_public_schema() -> None:
    """마이그레이션 계정은 테이블을 만들 수 있다"""
    with owner_engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == "kbos_owner"
        assert (
            connection.execute(
                text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            ).scalar_one()
            is True
        )


def test_default_privileges_grant_dml_to_app_role() -> None:
    """소유자가 만든 새 테이블에 앱 계정이 자동으로 읽기·쓰기 권한을 얻는다

    이게 없으면 S0-2가 테이블을 만드는 순간 모든 조회가
    permission denied로 실패한다 — 그런데 테이블이 0개인 지금은 증상이 없다.
    """
    probe = "default_priv_probe"
    # scratch_table()과 달리 여기서는 GRANT를 **일부러 하지 않는다** —
    # ALTER DEFAULT PRIVILEGES만으로 권한이 붙는지가 검증 대상이다.
    with owner_engine.begin() as connection:
        connection.execute(text(f'DROP TABLE IF EXISTS public."{probe}"'))
        connection.execute(text(f'CREATE TABLE public."{probe}" (i int)'))
    try:
        with engine.begin() as connection:
            connection.execute(text(f'INSERT INTO public."{probe}" VALUES (1)'))
            assert (
                connection.execute(text(f'SELECT count(*) FROM public."{probe}"')).scalar_one() == 1
            )
            connection.execute(text(f'UPDATE public."{probe}" SET i = 2'))
            connection.execute(text(f'DELETE FROM public."{probe}"'))
    finally:
        with owner_engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS public."{probe}"'))
