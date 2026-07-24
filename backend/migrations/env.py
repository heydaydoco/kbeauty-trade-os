"""alembic 실행 환경.

접속은 항상 kbos_owner(MIGRATION_DATABASE_URL)로 한다 — 런타임 계정 kbos_app은
DDL 권한이 없다(§17.5 / ADR-0002).

마이그레이션 왕복 검사처럼 다른 DB를 대상으로 하려면:
    alembic -x url=postgresql+psycopg://... upgrade head
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from app.core.config import settings
from app.core.db.session import build_engine

# app.registry가 모든 모델을 임포트해야 autogenerate가 테이블을 볼 수 있다.
from app.registry import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """-x url=... 이 있으면 그것, 없으면 설정의 마이그레이션 DSN."""
    override = context.get_x_argument(as_dictionary=True).get("url")
    if override:
        return override
    if settings.app_env == "test" and settings.test_migration_database_url is not None:
        return settings.test_migration_database_url.get_secret_value()
    return settings.migration_database_url.get_secret_value()


#: autogenerate 비교 옵션
#:
#: compare_server_default=False인 이유 — PostgreSQL은 now()를 CURRENT_TIMESTAMP로,
#: '1'을 1로 되돌려 읽어 실제 변경이 없어도 차이가 있다고 보고한다. 켜 두면
#: `alembic check`가 만성 적색이 되고, 만성 적색인 게이트는 아무도 안 본다.
#: 대신 "server_default 변경은 손으로 마이그레이션에 쓴다"를 규칙으로 둔다.
_COMPARE_OPTIONS = {
    "compare_type": True,
    "compare_server_default": False,
    "include_schemas": False,
    # 리비전 하나가 곧 트랜잭션 하나. 중간 실패 시 그 리비전만 롤백된다.
    "transaction_per_migration": True,
}


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_COMPARE_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = build_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            **_COMPARE_OPTIONS,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
