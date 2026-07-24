"""쿼리 계측 — 느린 쿼리 로그와 요청당 쿼리 수 (DESIGN.md §18.4 "N+1 금지·느린 쿼리 로그").

★ 바인드 파라미터는 절대 로그에 남기지 않는다.
  SQL 문장에는 값이 `%(param_1)s` 자리표시자로만 들어 있어 안전하지만,
  파라미터에는 비밀번호·주민번호·원가가 그대로 들어 있다. 아예 인자로 받지 않는다.
"""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.logging import get_logger
from app.core.logging.context import bump_query_count

logger = get_logger("app.db")

_WHITESPACE = re.compile(r"\s+")
MAX_SQL_LENGTH = 500

_registered = False


def _normalize_sql(statement: str) -> str:
    return _WHITESPACE.sub(" ", statement).strip()[:MAX_SQL_LENGTH]


def register_query_instrumentation() -> None:
    """엔진 전역에 계측을 건다. 두 번 불러도 한 번만 등록된다."""
    global _registered
    if _registered:
        return
    _registered = True

    @event.listens_for(Engine, "before_cursor_execute")
    def _before(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        conn.info.setdefault("_query_start", []).append(perf_counter())
        bump_query_count()

    @event.listens_for(Engine, "after_cursor_execute")
    def _after(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        stack = conn.info.get("_query_start")
        if not stack:
            return
        elapsed_ms = (perf_counter() - stack.pop()) * 1000
        if elapsed_ms >= settings.slow_query_ms:
            logger.warning(
                "slow_query",
                duration_ms=round(elapsed_ms, 1),
                threshold_ms=settings.slow_query_ms,
                sql=_normalize_sql(statement),
            )
