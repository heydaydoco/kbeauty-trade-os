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


# ── S1-1 백필 (ADR-0016 A2) ────────────────────────────────────────────────
#
# ★ 백필 코드는 "기존 행이 있을 때만" 도는데, 테스트 DB는 항상 비어 있어서
#   평범하게 head까지 올리는 검사로는 **한 번도 실행되지 않는다**. 실행된 적
#   없는 마이그레이션 코드는 실행된 적 없는 코드일 뿐이다(S0-1의 mako 필터가
#   정확히 그렇게 새 나갔다 — PROGRESS 주의 인계). 그래서 직전 리비전까지만
#   올려 행을 심고 나머지를 올린다.

#: S0-2의 skus(최소 컬럼) 리비전.
_BEFORE_PRODUCT_HIERARCHY = "65f7c2c5b6fa"


def test_backfill_links_existing_skus_to_generated_products() -> None:
    """S0-2에서 등록된 SKU가 있어도 S1-1 마이그레이션이 처방을 만들어 연결한다"""
    command.upgrade(_config(), _BEFORE_PRODUCT_HIERARCHY)
    engine = build_engine(_migration_check_url())
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO skus (sku_code, name_ko) VALUES ('LEG-001', '이월 세럼')")
        )

    command.upgrade(_config(), "head")

    with engine.connect() as connection:
        kind, product_name, description, brand_code = connection.execute(
            text(
                """
                SELECT s.kind, p.name_ko, p.description, b.brand_code
                FROM skus s
                JOIN products p ON p.id = s.product_id
                JOIN brands b ON b.id = p.brand_id
                WHERE s.sku_code = 'LEG-001'
                """
            )
        ).one()
    engine.dispose()

    assert kind == "SINGLE"
    assert product_name == "이월 세럼"
    assert brand_code == "UNCLASSIFIED"
    # 생성 출처가 남아야 사람이 만든 마스터와 구분되고, 구분돼야 정리된다.
    assert "자동 생성" in description
    assert "LEG-001" in description


def test_backfill_creates_nothing_on_an_empty_database() -> None:
    """SKU가 없으면 백필은 아무 행도 만들지 않는다 (새 설치에 유령 마스터 금지)"""
    command.upgrade(_config(), "head")

    engine = build_engine(_migration_check_url())
    with engine.connect() as connection:
        brands = connection.execute(text("SELECT count(*) FROM brands")).scalar_one()
        products = connection.execute(text("SELECT count(*) FROM products")).scalar_one()
    engine.dispose()

    assert (brands, products) == (0, 0)
