"""로그 프로세서 체인 — 순서가 곧 보안이다.

체인을 여기 한 곳에서만 조립한다. 두 곳에서 조립하면 한쪽만 고쳐져
"개발에서는 가려지는데 운영에서는 평문"이 된다.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.core.logging.context import add_request_context
from app.core.logging.redaction import redact_processor

Processor = Any


def shared_processors() -> list[Processor]:
    """structlog 로그와 표준 logging(uvicorn·sqlalchemy·alembic) 로그가 함께 지나는 체인.

    ★ 마지막 두 자리의 순서가 고정 요건이다.
        ... → format_exc_info → (컨텍스트 주입) → redact_processor → 렌더러
      format_exc_info가 예외를 문자열로 펼친 **뒤에** 마스킹해야
      트레이스백 안의 접속 문자열 비밀번호가 걸린다. 뒤집히면 조용히 새어 나가고
      테스트는 초록으로 남는다 — 그래서 메타 테스트가 이 순서를 검사한다.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        add_request_context,
        redact_processor,
    ]
