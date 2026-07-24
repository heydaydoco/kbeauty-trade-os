"""엔진·세션 팩토리.

두 가지를 구조로 강제한다.

1. **테스트가 개발 DB를 건드릴 수 없다.** APP_ENV=test면 접속 대상이 자동으로
   TEST_DATABASE_URL로 바뀐다. 테스트 격리는 TRUNCATE로 하는데(§ docs/testing.md),
   설정을 헷갈려 dev DB에 붙으면 개발 데이터가 통째로 날아간다.

2. **라우터가 트랜잭션을 열고 닫을 수 없다.** §17.1 "트랜잭션 시작·종료는
   서비스 레이어에서만"을 문서가 아니라 런타임이 강제한다 — GuardedSession은
   UnitOfWork 밖에서 commit/rollback을 호출하면 예외를 던진다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import ConfigurationError, settings


class TransactionBoundaryError(RuntimeError):
    """트랜잭션 경계를 서비스 레이어 밖에서 조작하려 했다 (§17.1)."""


class GuardedSession(Session):
    """UnitOfWork 밖에서의 commit/rollback을 거부하는 세션."""

    #: UnitOfWork가 커밋 직전에만 True로 올린다.
    _txn_control_allowed: bool = False

    def commit(self) -> None:
        self._require_boundary_owner("commit")
        super().commit()

    def rollback(self) -> None:
        self._require_boundary_owner("rollback")
        super().rollback()

    def _require_boundary_owner(self, action: str) -> None:
        if not self._txn_control_allowed:
            raise TransactionBoundaryError(
                f"세션의 {action}()을 직접 호출할 수 없습니다. "
                "하나의 업무 동작은 하나의 트랜잭션이며, 트랜잭션의 시작과 종료는 "
                "서비스 레이어의 unit_of_work()만 담당합니다 (DESIGN.md §17.1)."
            )


def _select_url(
    *, runtime: bool
) -> str:
    """실행 환경에 맞는 접속 문자열을 고른다."""
    if settings.app_env == "test":
        url = settings.test_database_url if runtime else settings.test_migration_database_url
        if url is None:
            name = "TEST_DATABASE_URL" if runtime else "TEST_MIGRATION_DATABASE_URL"
            raise ConfigurationError(
                f"APP_ENV=test인데 {name}이 없습니다. "
                "테스트는 전용 DB(kbos_test)에서만 돌아야 합니다 — 개발 DB에 붙으면 "
                "격리용 TRUNCATE가 개발 데이터를 지웁니다."
            )
        return url.get_secret_value()
    source = settings.database_url if runtime else settings.migration_database_url
    return source.get_secret_value()


def build_engine(url: str, **overrides: Any) -> Engine:
    """공통 엔진 설정.

    격리 수준은 READ COMMITTED로 명시한다 — §17.2가 전면 SERIALIZABLE을 금지하고
    잔량을 깨뜨리는 지점만 행 잠금으로 직렬화하도록 규정한다.

    statement_timeout·lock_timeout 등은 엔진이 아니라 DB 역할에 붙어 있다
    (infra/postgres/init/00-roles.sql). psql·pgAdmin 등 앱을 거치지 않는
    접속에도 똑같이 걸리게 하려면 그쪽이 유일한 출처여야 한다.
    """
    options: dict[str, Any] = {
        "isolation_level": "READ COMMITTED",
        "pool_pre_ping": True,
        "future": True,
        "echo": False,
    }
    # poolclass를 지정한 호출(예: alembic의 NullPool)에는 풀 크기 인자를 넘기면
    # create_engine이 TypeError를 낸다 — NullPool은 그 인자를 받지 않는다.
    if "poolclass" not in overrides:
        options["pool_size"] = 10
        options["max_overflow"] = 10
    options.update(overrides)
    return create_engine(url, **options)


#: 런타임 엔진 — kbos_app 계정. 테이블을 만들 수 없다.
engine: Engine = build_engine(_select_url(runtime=True))

#: 마이그레이션·테스트 정리용 엔진 — kbos_owner 계정.
#: 런타임 코드에서 쓰면 §17.5의 불변 강제가 무의미해진다. alembic과
#: 테스트 픽스처(TRUNCATE)만 사용한다.
owner_engine: Engine = build_engine(_select_url(runtime=False), pool_size=2, max_overflow=2)

SessionFactory = sessionmaker(
    bind=engine,
    class_=GuardedSession,
    autoflush=False,
    expire_on_commit=False,
)
