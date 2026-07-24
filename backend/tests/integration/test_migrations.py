"""K. 보안·품질 — 마이그레이션 드라이런 (DESIGN.md §18.3).

"회귀를 기계가 잡는다"를 실제로 달성하는 4종. 앱 검사와 **다른 DB**(kbos_migr)에서
왕복해서 pytest 스키마를 건드리지 않는다.
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.core.config import settings
from app.core.db.session import build_engine
from app.registry import Base
from tests.conftest import ALEMBIC_INI

pytestmark = pytest.mark.group_k


def _migration_check_url() -> str:
    assert settings.migration_check_database_url is not None
    return settings.migration_check_database_url.get_secret_value()


def _config() -> AlembicConfig:
    return AlembicConfig(str(ALEMBIC_INI))


@pytest.fixture(autouse=True)
def _isolate_migration_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 파일의 alembic 명령을 kbos_migr(왕복 검사 전용 DB)로 향하게 한다.

    env.py가 ALEMBIC_DATABASE_URL 환경변수를 우선 본다. 시작 상태를 빈 DB로
    맞춰서 각 테스트가 독립적으로 upgrade부터 시작한다.
    """
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", _migration_check_url())
    command.downgrade(_config(), "base")


def test_single_head() -> None:
    """마이그레이션 head는 정확히 하나다 (병렬 브랜치 충돌 방지)"""
    script = ScriptDirectory.from_config(_config())
    assert len(script.get_heads()) == 1


def test_upgrade_from_empty() -> None:
    """빈 DB에서 head까지 올라간다"""
    command.upgrade(_config(), "head")
    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    engine.dispose()
    script = ScriptDirectory.from_config(_config())
    assert revision == script.get_current_head()


def test_downgrade_then_upgrade_roundtrip() -> None:
    """올렸다 내렸다 다시 올려도 성공한다 (downgrade가 실제로 되돌린다)"""
    command.upgrade(_config(), "head")
    command.downgrade(_config(), "base")
    command.upgrade(_config(), "head")  # 예외 없이 끝나면 통과


def test_no_model_migration_drift() -> None:
    """모델과 마이그레이션이 일치한다 (모델만 고치고 마이그레이션을 잊은 경우 검출)"""
    command.upgrade(_config(), "head")
    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": False}
        )
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], f"모델↔마이그레이션 드리프트: {diff}"


def test_migration_runs_as_owner_role() -> None:
    """마이그레이션은 kbos_owner로 접속한다"""
    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == "kbos_owner"
    engine.dispose()
