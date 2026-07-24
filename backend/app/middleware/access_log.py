"""접근 로그 — uvicorn 기본 로그 대신 이걸 쓴다.

uvicorn의 접근 로그는 우리 프로세서 체인을 안 타서 마스킹이 걸리지 않는다.
그래서 그쪽은 끄고(app/core/logging/__init__.py) 여기서 남긴다.

헬스체크는 몇 초마다 들어오므로 정상 응답은 DEBUG로 낮춘다. 다만 **완전히
침묵시키지는 않는다** — 준비 실패(503)와 상태가 바뀌는 순간은 올려서 남긴다.
장애 추적에서 "언제부터 안 됐나"를 답할 수 있어야 한다.
"""

from __future__ import annotations

from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.core.logging.context import current_query_count

logger = get_logger("app.access")

#: 정상 응답을 DEBUG로 낮출 경로
QUIET_PATHS = frozenset({"/api/v1/system/healthz", "/api/v1/system/readyz"})

_last_status: dict[str, int] = {}


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500

        async def send_capturing_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_capturing_status)
        finally:
            self._log(scope, status_code, (perf_counter() - started) * 1000)

    def _log(self, scope: Scope, status_code: int, duration_ms: float) -> None:
        path = scope.get("path", "")
        method = scope.get("method", "")
        level = self._level_for(path, status_code)
        getattr(logger, level)(
            "http_request",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=round(duration_ms, 1),
            db_query_count=current_query_count(),
        )

    def _level_for(self, path: str, status_code: int) -> str:
        changed = _last_status.get(path) not in (None, status_code)
        _last_status[path] = status_code
        if status_code >= 500:
            return "error"
        if status_code >= 400:
            return "warning"
        if path in QUIET_PATHS and not changed:
            return "debug"
        return "info"
