"""FastAPI 애플리케이션 조립."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import settings
from app.core.db.events import register_query_instrumentation
from app.core.errors.handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.request_context import RequestContextMiddleware


def create_app() -> FastAPI:
    configure_logging(settings)
    register_query_instrumentation()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        # 운영에서는 API 문서를 열지 않는다(내부 구조 노출).
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )

    # ★ add_middleware는 스택 앞쪽에 끼워 넣는다 — 마지막에 추가한 것이 가장 바깥이다.
    #   접근 로그가 request_id를 찍으려면 RequestContext가 더 바깥이어야 하므로
    #   AccessLog를 먼저 추가한다.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(
        RequestContextMiddleware, trust_incoming=settings.trust_incoming_request_id
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    get_logger("app").info(
        "application_started", app_env=settings.app_env, version=settings.app_version
    )
    return app


app = create_app()
