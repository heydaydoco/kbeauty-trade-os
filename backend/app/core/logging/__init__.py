"""로깅 설정 (DESIGN.md §2 운영 품질, §18.1, §18.4).

출력은 표준출력 한 곳. dev는 사람이 읽는 형식, 운영은 JSON 한 줄.
uvicorn·sqlalchemy·alembic이 남기는 표준 logging 기록도 같은 프로세서 체인을
지나게 해서 마스킹에 구멍이 생기지 않게 한다.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import structlog

from app.core.logging.processors import shared_processors
from app.core.logging.redaction import register_secret_values

if TYPE_CHECKING:
    from app.core.config import Settings

__all__ = ["configure_logging", "get_logger"]

get_logger = structlog.stdlib.get_logger


def configure_logging(settings: Settings) -> None:
    shared = shared_processors()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: object
    if settings.is_prod:
        # ensure_ascii=False — 한국어가 \uXXXX로 깨지면 로그를 사람이 못 읽는다.
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    formatter = structlog.stdlib.ProcessorFormatter(
        # 표준 logging으로 들어온 기록도 같은 체인을 태운다.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # uvicorn의 자체 접근 로그는 끈다 — 우리 미들웨어가 request_id와 함께
    # 마스킹된 형태로 남긴다(두 벌이 남으면 한 벌은 마스킹을 안 거친다).
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("uvicorn.error", "sqlalchemy.engine", "alembic"):
        logging.getLogger(noisy).setLevel(
            logging.INFO if settings.log_level == "DEBUG" else logging.WARNING
        )

    # 설정에 든 진짜 비밀 값을 마스킹 대상으로 등록한다.
    register_secret_values(
        [
            settings.secret_key.get_secret_value(),
            settings.database_url.get_secret_value(),
            settings.migration_database_url.get_secret_value(),
        ]
    )
