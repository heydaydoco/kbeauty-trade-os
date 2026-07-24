"""헬스체크 (DESIGN.md §2 운영 품질).

두 가지는 다른 질문에 답한다.

  healthz  — 프로세스가 살아 있나? (DB에 의존하지 않는다. 항상 200)
             DB가 잠깐 끊겼다고 컨테이너를 재시작하면 복구를 더 늦춘다.
  readyz   — 지금 요청을 받아도 되나? (DB 연결 + 마이그레이션 최신 여부)

★ 응답에 담지 않는 것: 접속 문자열, 호스트, 계정명, 리비전 해시, 예외 원문.
  헬스 엔드포인트는 대개 인증 없이 노출되므로 내부 구조를 알려 주면 안 된다.
  같은 사건의 **로그에는** 마스킹된 상세가 남는다(§18.4 이원화).

여기의 "헬스체크"는 인프라 점검이다. §14 ⑭의 "데이터 완성도 헬스체크"
(HS 없는 SKU·MSDS 없는 DG 등)는 전혀 다른 도메인 기능이며 Phase 6 범위다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.core.config import settings
from app.core.db.session import engine
from app.core.logging import get_logger
from app.core.time import utcnow

logger = get_logger("app.health")

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

STATUS_OK = "ok"
STATUS_ERROR = "error"


def liveness() -> dict[str, Any]:
    return {"status": STATUS_OK, "checked_at": utcnow().isoformat()}


def _check_database() -> tuple[bool, dict[str, str]]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("readiness_db_failed", exc_info=exc)
        return False, {"status": "unavailable"}
    return True, {"status": STATUS_OK}


def _check_migration() -> tuple[bool, dict[str, str]]:
    try:
        script = ScriptDirectory.from_config(AlembicConfig(str(ALEMBIC_INI)))
        expected = script.get_current_head()
        with engine.connect() as connection:
            applied = MigrationContext.configure(connection).get_current_revision()
    except Exception as exc:
        logger.error("readiness_migration_check_failed", exc_info=exc)
        return False, {"status": "unknown"}

    if applied != expected:
        # 리비전 해시는 로그에만 남긴다.
        logger.error("readiness_migration_outdated", applied=applied, expected=expected)
        return False, {"status": "outdated"}
    return True, {"status": STATUS_OK}


def readiness() -> tuple[bool, dict[str, Any]]:
    db_ok, db_detail = _check_database()
    migration_ok, migration_detail = (_check_migration() if db_ok else (False, {"status": "unknown"}))
    ready = db_ok and migration_ok
    return ready, {
        "status": STATUS_OK if ready else STATUS_ERROR,
        "app_env": settings.app_env,
        "version": settings.app_version,
        "checks": {"db": db_detail, "migration": migration_detail},
        "checked_at": utcnow().isoformat(),
    }
